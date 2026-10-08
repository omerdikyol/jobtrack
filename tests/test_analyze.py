import json

import pytest

from jobtrack.analyze import Analyzer
from jobtrack.llm import LLMError
from tests.helpers import make_message

REJECTED = make_message(
    "Update on your application",
    "Unfortunately we are not moving forward with other candidates.",
)
# No rule matches this: the model is the only thing that can read it.
UNUSUAL = make_message(
    "Following up on our conversation",
    "Thanks for the chat. The role has now been filled internally.",
)
NOISE = make_message("Your order has shipped", "Tracking number inside.")


class FakeLLM:
    """Scripted stand-in for OllamaLLM."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.max_chars = 4000

    def complete(self, system, user):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else "{}"
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(event="rejected", company=None, role=None, confidence=0.9, job_related=True):
    return json.dumps(
        {
            "job_related": job_related,
            "event": event,
            "company": company,
            "role": role,
            "confidence": confidence,
        }
    )


# --------------------------------------------------------------------------
# off
# --------------------------------------------------------------------------
def test_off_mode_never_calls_the_model():
    llm = FakeLLM([reply()])
    analyzer = Analyzer(llm, mode="off")

    result, source = analyzer.analyze(UNUSUAL)

    assert llm.calls == 0
    assert source == "rules"
    assert result is None
    assert analyzer.llm_active is False


@pytest.mark.parametrize("mode", ["nonsense", "ON", ""])
def test_unknown_mode_is_rejected(mode):
    with pytest.raises(ValueError):
        Analyzer(None, mode=mode)


# --------------------------------------------------------------------------
# fallback
# --------------------------------------------------------------------------
def test_fallback_skips_the_model_when_rules_are_confident():
    llm = FakeLLM([reply(event="offer")])
    analyzer = Analyzer(llm, mode="fallback")

    result, source = analyzer.analyze(REJECTED)

    assert source == "rules"
    assert result.kind == "rejected"
    assert llm.calls == 0


def test_fallback_asks_the_model_when_rules_find_nothing():
    llm = FakeLLM([reply(event="rejected", company="Initech")])
    analyzer = Analyzer(llm, mode="fallback")

    result, source = analyzer.analyze(UNUSUAL)

    assert source == "llm"
    assert result.kind == "rejected"
    assert result.company == "Initech"
    assert llm.calls == 1


def test_fallback_keeps_the_rule_result_when_the_model_says_not_relevant():
    llm = FakeLLM([reply(job_related=False)])
    analyzer = Analyzer(llm, mode="fallback")

    result, source = analyzer.analyze(UNUSUAL)

    assert result is None
    assert source == "rules"


def test_fallback_does_not_let_the_model_delete_a_confident_rule_match():
    llm = FakeLLM([reply(job_related=False)])
    analyzer = Analyzer(llm, mode="fallback", confidence_floor=0.99)

    result, source = analyzer.analyze(REJECTED)

    assert source == "rules"
    assert result.kind == "rejected"


# --------------------------------------------------------------------------
# always
# --------------------------------------------------------------------------
def test_always_trusts_the_model_over_the_rules():
    llm = FakeLLM([reply(event="offer", confidence=0.95)])
    analyzer = Analyzer(llm, mode="always")

    result, source = analyzer.analyze(REJECTED)

    assert source == "llm"
    assert result.kind == "offer"


def test_always_drops_what_the_model_calls_irrelevant():
    llm = FakeLLM([reply(job_related=False)])
    analyzer = Analyzer(llm, mode="always")

    result, source = analyzer.analyze(REJECTED)

    assert result is None
    assert source == "llm"


# --------------------------------------------------------------------------
# failure handling
# --------------------------------------------------------------------------
def test_model_errors_fall_back_to_rules():
    llm = FakeLLM([LLMError("boom"), LLMError("boom"), LLMError("boom")])
    analyzer = Analyzer(llm, mode="always", max_failures=3)

    result, source = analyzer.analyze(REJECTED)

    assert source == "rules"
    assert result.kind == "rejected"


def test_repeated_failures_disable_the_model_for_the_rest_of_the_run():
    llm = FakeLLM([LLMError("boom")] * 5)
    analyzer = Analyzer(llm, mode="always", max_failures=2)

    analyzer.analyze(UNUSUAL)
    analyzer.analyze(UNUSUAL)
    assert analyzer.disabled_reason is not None

    calls_before = llm.calls
    analyzer.analyze(UNUSUAL)
    assert llm.calls == calls_before  # no further attempts
    assert analyzer.llm_active is False


def test_a_success_resets_the_failure_streak():
    llm = FakeLLM([LLMError("boom"), reply(event="rejected"), LLMError("boom")])
    analyzer = Analyzer(llm, mode="always", max_failures=2)

    analyzer.analyze(UNUSUAL)
    analyzer.analyze(UNUSUAL)
    analyzer.analyze(UNUSUAL)

    assert analyzer.disabled_reason is None
    assert analyzer.llm_active is True


def test_rules_are_used_when_no_model_is_supplied():
    analyzer = Analyzer(None, mode="always")

    result, source = analyzer.analyze(REJECTED)

    assert source == "rules"
    assert result.kind == "rejected"


def test_fallback_does_not_spend_a_model_call_on_confident_noise():
    llm = FakeLLM([reply()])
    analyzer = Analyzer(llm, mode="fallback")

    result, source = analyzer.analyze(NOISE)

    assert result is None
    assert source == "rules"
    assert llm.calls == 0


def test_fallback_escalates_mail_the_rules_cannot_judge():
    """Absence of job vocabulary is not a verdict — that is the model's cue."""
    obscure = make_message(
        "Re: our chat",
        "Great speaking with you. Sadly we went with somebody else in the end.",
    )
    llm = FakeLLM([reply(event="rejected", company="Initech")])
    analyzer = Analyzer(llm, mode="fallback")

    result, source = analyzer.analyze(obscure)

    assert source == "llm"
    assert result.kind == "rejected"
    assert llm.calls == 1


def test_fallback_escalates_when_rules_are_only_weakly_confident():
    # "position has been filled" does match a rule, but scores only 0.33.
    message = make_message("Re: our chat", "The position has been filled internally.")
    llm = FakeLLM([reply(event="rejected", company="Initech", confidence=0.95)])
    analyzer = Analyzer(llm, mode="fallback")

    result, source = analyzer.analyze(message)

    assert source == "llm"
    assert result.confidence == 0.95
    assert llm.calls == 1


def test_merge_keeps_the_company_the_rules_found_when_the_model_omits_it():
    message = make_message(
        "Update on your application to Globex",
        "Unfortunately we have decided to move forward with other candidates.",
    )
    analyzer = Analyzer(
        FakeLLM([reply(event="rejected", company=None, role=None)]), mode="always"
    )

    result, source = analyzer.analyze(message)

    assert source == "llm"
    assert result.kind == "rejected"
    assert result.company == "Globex"


@pytest.mark.parametrize(
    "model_reply",
    [
        reply(event="interview", company="Upwork", role="Software Engineer"),
        reply(event="interview", company="Upwork", role=None),
        reply(event=None, job_related=False),
    ],
)
def test_model_cannot_replace_or_drop_explicit_upwork_invitation(model_reply):
    msg = make_message(
        "Invitation to Interview for: Interview Process",
        sender="Upwork <notify@upwork.com>",
    )
    result, _ = Analyzer(FakeLLM([model_reply]), mode="always").analyze(msg)
    assert result.kind == "interview"
    assert result.role == "Interview Process"
