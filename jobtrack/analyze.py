"""Combine the rule engine with the optional LLM pass.

Rules are cheap and predictable; the model catches wording nobody wrote a rule
for. Which one wins depends on the mode:

``off``
    Rules only. Never talks to the model.
``fallback``
    Rules run first and the model is consulted only when the rules are unsure
    (no match, or low confidence). A model that says "not job related" cannot
    delete an email the rules classified confidently.
``always``
    The model decides every message, including dropping ones it considers
    irrelevant. Slower, but the most adaptive.

A model that keeps failing is switched off for the rest of the run so a dead
Ollama server degrades to plain rules instead of stalling every message.
"""

from __future__ import annotations

import os

from jobtrack.classify import (
    RELEVANCE_IRRELEVANT,
    classify,
    relevance,
    upwork_invitation_role,
)
from jobtrack.llm import LLMConfig, LLMError, build_llm, classify_message
from jobtrack.models import Classification, EmailMessage

MODES = ("off", "auto", "fallback", "always")

# Below this, we do not trust the rules enough to skip the model.
RULES_CONFIDENCE_FLOOR = 0.4


def _merge(rules: Classification | None, llm_result: Classification) -> Classification:
    """Prefer the model's verdict, but keep rule-found names it left empty."""
    if rules is None:
        return llm_result
    llm_result.company = llm_result.company or rules.company
    llm_result.role = llm_result.role or rules.role
    if llm_result.kind == rules.kind:
        llm_result.matched = ["llm", *rules.matched]
    return llm_result


class Analyzer:
    """Classifies emails with rules, optionally assisted by a local model."""

    def __init__(
        self,
        llm=None,
        mode: str = "fallback",
        max_failures: int = 3,
        confidence_floor: float = RULES_CONFIDENCE_FLOOR,
    ):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}, expected one of {MODES}")
        self.llm = llm
        self.mode = mode
        self.max_failures = max_failures
        self.confidence_floor = confidence_floor
        self.failures = 0
        self.last_error: str | None = None
        self.disabled_reason: str | None = None
        self.llm_calls = 0
        self.context = ""
        self.last_review = None

    def set_context(self, context: str) -> None:
        self.context = context

    @property
    def llm_active(self) -> bool:
        return (
            self.llm is not None and self.disabled_reason is None and self.mode != "off"
        )

    @property
    def cache_key(self) -> str:
        # Record the rules, prompt, and model revision for analysis provenance.
        # Previously processed messages are revisited only with an explicit full sync.
        if not self.llm_active:
            return "rules-v7"
        return f"rules-v7:prompt-v6:{self.mode}:{self.confidence_floor}:{self.llm.provider}:{self.llm.model}:{self.llm.max_chars}"

    def analyze(self, msg: EmailMessage) -> tuple[Classification | None, str]:
        """Return ``(classification, source)`` where source is rules or llm."""
        rules = classify(msg)
        invitation_role = upwork_invitation_role(msg)
        self.last_review = None

        if not self.llm_active:
            return rules, "rules"

        if self.mode == "fallback":
            # Confidently unrelated mail (receipts, order confirmations) is not
            # worth a model call. "No job vocabulary at all" is worth one.
            if relevance(msg) == RELEVANCE_IRRELEVANT:
                return None, "rules"
            if rules is not None and rules.confidence >= self.confidence_floor:
                return rules, "rules"

        try:
            self.llm_calls += 1
            if callable(getattr(self.llm, "review", None)):
                llm_result = self.llm.review(msg, self.context)
                self.last_review = self.llm.last_review
                if self.last_review["status"] == "unresolved":
                    failed = any(
                        "error" in o for o in self.last_review["rounds"][-1]["opinions"]
                    )
                    self.failures = self.failures + 1 if failed else 0
                    if self.failures >= self.max_failures:
                        self.disabled_reason = "Consensus reviewers repeatedly failed; rules were retained."
                    if rules:
                        rules.review = self.last_review
                    return rules, "rules"
            elif self.context:
                from jobtrack.council import REVIEW_PROMPT
                from jobtrack.llm import extract_json, interpret, render_email

                llm_result = interpret(
                    extract_json(
                        self.llm.complete(
                            REVIEW_PROMPT,
                            f"PRIOR RELATED EMAILS (context only):\n{self.context}\n\nCURRENT EMAIL:\n{render_email(msg, self.llm.max_chars)}",
                        )
                    )
                )
            else:
                llm_result = classify_message(msg, self.llm)
            self.failures = 0
        except LLMError as exc:
            self.failures += 1
            self.last_error = str(exc)
            if self.failures >= self.max_failures:
                self.disabled_reason = str(exc)
            return rules, "rules"

        if llm_result is None:
            if invitation_role:
                return rules, "rules"
            # `always` treats the model's "not job related" as final; `fallback`
            # keeps whatever the rules found instead.
            if self.mode == "always":
                return None, "llm"
            return rules, "rules"

        # Either way the model's verdict wins, but names it left blank are
        # filled in from the rules rather than thrown away.
        result = _merge(rules, llm_result)
        if invitation_role:
            result.role = invitation_role
        return result, "llm"


def build_analyzer(
    config: LLMConfig | None = None,
    mode: str | None = None,
    on_note=None,
) -> Analyzer:
    """Resolve the LLM mode, probing the provider when the mode is 'auto'.

    Precedence is: explicit argument, settings UI, environment, then auto.

    ``on_note`` receives a short human-readable explanation of what happened,
    so the CLI can print it to stderr and the web app can log it.
    """
    from jobtrack.settings import load as load_settings

    note = on_note or (lambda _message: None)
    resolved = (
        mode
        or load_settings().llm_mode
        or os.environ.get("JOBTRACK_LLM_MODE")
        or "auto"
    )

    if resolved not in MODES:
        note(f"Unknown llm mode {resolved!r}; using rules only.")
        return Analyzer(None, "off")

    if resolved == "off":
        return Analyzer(None, "off")

    stored = load_settings()
    if config is None and stored.review_strategy == "consensus":
        from jobtrack.council import configured_council

        llm = configured_council(stored)
    elif config is None and stored.review_models:
        selection = stored.review_models[0]
        llm = build_llm(
            LLMConfig.for_provider(selection["provider"], selection["model"])
        )
    else:
        llm = build_llm(config or LLMConfig.load())

    if resolved == "auto":
        usable, explanation = llm.check()
        if not usable:
            note(f"LLM off — {explanation}")
            return Analyzer(None, "off")
        note(explanation)
        return Analyzer(llm, "fallback")

    return Analyzer(llm, resolved)
