import io
import urllib.error

import pytest

from jobtrack import llm as llm_mod
from jobtrack.llm import (
    LLMConfig,
    LLMError,
    OllamaLLM,
    extract_json,
    interpret,
    render_email,
)
from tests.helpers import make_message


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------
def test_extract_json_plain():
    assert extract_json('{"event": "offer"}') == {"event": "offer"}


def test_extract_json_strips_code_fence():
    assert extract_json('```json\n{"event": "offer"}\n```') == {"event": "offer"}


def test_extract_json_tolerates_surrounding_prose():
    raw = 'Sure! Here is the answer:\n{"event": "rejected", "confidence": 0.9}\nHope that helps.'
    assert extract_json(raw) == {"event": "rejected", "confidence": 0.9}


@pytest.mark.parametrize("raw", ["no json here", '{"broken": ', "[1, 2, 3]"])
def test_extract_json_rejects_unusable_replies(raw):
    with pytest.raises(LLMError):
        extract_json(raw)


# --------------------------------------------------------------------------
# Interpreting the model's answer
# --------------------------------------------------------------------------
def payload(**overrides):
    base = {
        "job_related": True,
        "event": "rejected",
        "company": "Initech",
        "role": "Backend Engineer",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def test_interpret_builds_a_classification():
    result = interpret(payload())
    assert result.kind == "rejected"
    assert result.company == "Initech"
    assert result.role == "Backend Engineer"
    assert result.confidence == 0.9
    assert result.matched == ["llm"]


def test_interpret_honours_not_job_related():
    assert interpret(payload(job_related=False)) is None


@pytest.mark.parametrize("event", [None, "unknown", "spam", 42, ""])
def test_interpret_requires_a_known_event(event):
    assert interpret(payload(event=event)) is None


def test_interpret_rejects_job_board_as_company():
    assert interpret(payload(company="Greenhouse")).company is None
    assert interpret(payload(company="LinkedIn")).company is None


@pytest.mark.parametrize("value", ["", "  ", "N/A", "unknown", "null", "-", None, 5])
def test_interpret_rejects_placeholder_names(value):
    result = interpret(payload(company=value, role=value))
    assert result.company is None
    assert result.role is None


def test_interpret_drops_company_that_duplicates_the_role():
    result = interpret(payload(company="Backend Engineer", role="Backend Engineer"))
    assert result.company is None
    assert result.role == "Backend Engineer"


def test_interpret_clamps_and_defaults_confidence():
    assert interpret(payload(confidence=3)).confidence == 1.0
    assert interpret(payload(confidence=-1)).confidence == 0.0
    assert interpret(payload(confidence="high")).confidence == 0.7
    # bools are ints in Python and must not be read as 1/0.
    assert interpret(payload(confidence=True)).confidence == 0.7


def test_interpret_normalises_event_case():
    assert interpret(payload(event="REJECTED")).kind == "rejected"


# --------------------------------------------------------------------------
# Ollama transport
# --------------------------------------------------------------------------
def test_complete_returns_message_content(monkeypatch):
    captured = {}

    def fake(url, data, timeout, headers=None):
        captured.update(url=url, data=data, timeout=timeout, headers=headers)
        return {"message": {"content": '{"event": "offer"}'}}

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = OllamaLLM(LLMConfig(model="qwen2.5:3b", base_url="http://localhost:11434"))

    assert llm.complete("sys", "user") == '{"event": "offer"}'
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["data"]["model"] == "qwen2.5:3b"
    assert captured["data"]["stream"] is False
    assert captured["data"]["format"] == "json"
    assert captured["data"]["messages"][0]["role"] == "system"


def test_complete_explains_a_missing_model(monkeypatch):
    def fake(url, data, timeout, headers=None):
        raise urllib.error.HTTPError(
            url, 404, "Not Found", {}, io.BytesIO(b"model not found")
        )

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = OllamaLLM(LLMConfig(model="qwen2.5:3b"))

    with pytest.raises(LLMError, match="ollama pull qwen2.5:3b"):
        llm.complete("sys", "user")


def test_complete_explains_an_unreachable_server(monkeypatch):
    def fake(url, data, timeout, headers=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = OllamaLLM(LLMConfig())

    with pytest.raises(LLMError, match="ollama serve"):
        llm.complete("sys", "user")


def test_check_reports_unreachable_server(monkeypatch):
    def fake(url, data, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    usable, explanation = OllamaLLM(LLMConfig()).check()

    assert usable is False
    assert "Cannot reach Ollama" in explanation


def test_check_reports_no_models(monkeypatch):
    monkeypatch.setattr(llm_mod, "_request_json", lambda *a, **k: {"models": []})
    usable, explanation = OllamaLLM(LLMConfig()).check()

    assert usable is False
    assert "no models" in explanation


def test_check_accepts_a_different_tag_for_the_same_model(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "_request_json",
        lambda *a, **k: {"models": [{"name": "qwen2.5:3b-instruct-q4_K_M"}]},
    )
    usable, explanation = OllamaLLM(LLMConfig(model="qwen2.5:3b")).check()

    assert usable is True
    assert "qwen2.5:3b" in explanation


def test_check_reports_a_model_that_was_never_pulled(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "_request_json", lambda *a, **k: {"models": [{"name": "llama3.2"}]}
    )
    usable, explanation = OllamaLLM(LLMConfig(model="qwen2.5:3b")).check()

    assert usable is False
    assert "ollama pull qwen2.5:3b" in explanation


# --------------------------------------------------------------------------
# Prompt rendering
# --------------------------------------------------------------------------
def test_render_email_includes_headers_and_body():
    message = make_message(
        "Your application", "Thanks for applying.", sender="A <a@b.com>"
    )
    rendered = render_email(message, max_chars=1000)

    assert "Subject: Your application" in rendered
    assert "Thanks for applying." in rendered


def test_render_email_truncates_long_bodies():
    message = make_message("Subject", "x" * 500)
    rendered = render_email(message, max_chars=50)

    assert "[truncated]" in rendered
    assert len(rendered) < 500


def test_config_from_env_and_settings(monkeypatch):
    monkeypatch.setenv("JOBTRACK_LLM_MODEL", "llama3.2")
    monkeypatch.setenv("JOBTRACK_LLM_BASE_URL", "http://box:11434/")
    monkeypatch.setenv("JOBTRACK_LLM_MAX_CHARS", "1234")

    config = LLMConfig.load()

    assert config.model == "llama3.2"
    assert config.base_url == "http://box:11434"
    assert config.max_chars == 1234


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "provider, expected_model, expected_base",
    [
        ("groq", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1"),
        (
            "gemini",
            "gemini-3.1-flash-lite",
            "https://generativelanguage.googleapis.com/v1beta",
        ),
        ("ollama", "qwen2.5:3b", "http://localhost:11434"),
    ],
)
def test_a_provider_name_fills_in_its_own_defaults(
    provider, expected_model, expected_base
):
    config = LLMConfig(provider=provider)

    assert config.model == expected_model
    assert config.base_url == expected_base


def test_explicit_values_beat_the_provider_defaults():
    config = LLMConfig(provider="groq", model="my-model", base_url="http://box/v1")

    assert config.model == "my-model"
    assert config.base_url == "http://box/v1"


def test_the_api_key_comes_from_the_provider_s_environment_variable(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert LLMConfig(provider="groq").api_key == "gsk-test"


def test_an_explicit_api_key_wins(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    assert LLMConfig(provider="groq", api_key="explicit").api_key == "explicit"


def test_build_llm_picks_the_right_client():
    from jobtrack.llm import GeminiLLM, OllamaLLM, OpenAICompatLLM, build_llm

    assert isinstance(build_llm(LLMConfig(provider="ollama")), OllamaLLM)
    assert isinstance(build_llm(LLMConfig(provider="gemini", api_key="k")), GeminiLLM)
    for provider in ("groq", "openai", "cerebras", "openrouter", "local"):
        assert isinstance(
            build_llm(LLMConfig(provider=provider, api_key="k")), OpenAICompatLLM
        )


def test_build_llm_rejects_an_unknown_provider():
    from jobtrack.llm import build_llm

    with pytest.raises(LLMError, match="Unknown provider"):
        build_llm(LLMConfig(provider="nope"))


def test_openai_compatible_provider_is_not_usable_without_a_key():
    from jobtrack.llm import OpenAICompatLLM, build_llm

    llm = build_llm(LLMConfig(provider="groq", api_key=""))
    usable, explanation = llm.check()

    assert isinstance(llm, OpenAICompatLLM)
    assert usable is False
    assert "GROQ_API_KEY" in explanation


def test_gemini_is_not_usable_without_a_key():
    from jobtrack.llm import GeminiLLM

    usable, explanation = GeminiLLM(LLMConfig(provider="gemini", api_key="")).check()

    assert usable is False
    assert "GEMINI_API_KEY" in explanation


# --------------------------------------------------------------------------
# OpenAI-compatible transport
# --------------------------------------------------------------------------
def test_openai_compatible_request_shape(monkeypatch):
    from jobtrack.llm import OpenAICompatLLM

    captured = {}

    def fake(url, data, timeout, headers=None):
        captured.update(url=url, payload=data, headers=headers)
        return {"choices": [{"message": {"content": '{"event":"rejected"}'}}]}

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = OpenAICompatLLM(LLMConfig(provider="groq", api_key="gsk-x"))

    assert llm.complete("sys", "user") == '{"event":"rejected"}'
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer gsk-x"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["messages"][1] == {"role": "user", "content": "user"}


def test_openai_compatible_retries_without_json_mode(monkeypatch):
    """Some compatible servers reject response_format; the prompt still asks for JSON."""
    from jobtrack.llm import OpenAICompatLLM

    seen = []

    def fake(url, data, timeout, headers=None):
        seen.append("response_format" in data)
        if "response_format" in data:
            raise urllib.error.HTTPError(
                url, 400, "Bad Request", {}, io.BytesIO(b"response_format unsupported")
            )
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = OpenAICompatLLM(LLMConfig(provider="local", api_key="x"))

    assert llm.complete("sys", "user") == "{}"
    assert seen == [True, False]


def test_openai_compatible_explains_a_rejected_key(monkeypatch):
    from jobtrack.llm import OpenAICompatLLM

    def fake(url, data, timeout, headers=None):
        raise urllib.error.HTTPError(
            url, 401, "Unauthorized", {}, io.BytesIO(b"bad key")
        )

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = OpenAICompatLLM(LLMConfig(provider="groq", api_key="nope"))

    with pytest.raises(LLMError, match="rejected the API key"):
        llm.complete("sys", "user")


def test_rate_limiting_is_explained(monkeypatch):
    from jobtrack.llm import OpenAICompatLLM

    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def fake(url, data, timeout, headers=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url, 429, "Too Many Requests", {}, io.BytesIO(b"slow down")
        )

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = OpenAICompatLLM(LLMConfig(provider="groq", api_key="k"))

    with pytest.raises(LLMError, match="rate limiting"):
        llm.complete("sys", "user")
    assert calls["n"] == 3  # it retried before giving up


# --------------------------------------------------------------------------
# Gemini transport
# --------------------------------------------------------------------------
def test_gemini_request_shape(monkeypatch):
    from jobtrack.llm import GeminiLLM

    captured = {}

    def fake(url, data, timeout, headers=None):
        captured.update(url=url, payload=data, headers=headers)
        return {"candidates": [{"content": {"parts": [{"text": '{"event":"offer"}'}]}}]}

    monkeypatch.setattr(llm_mod, "_request_json", fake)
    llm = GeminiLLM(LLMConfig(provider="gemini", api_key="g-key"))

    assert llm.complete("sys", "user") == '{"event":"offer"}'
    assert captured["url"].endswith("/models/gemini-3.1-flash-lite:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "g-key"
    assert (
        captured["payload"]["generationConfig"]["responseMimeType"]
        == "application/json"
    )
    assert captured["payload"]["systemInstruction"]["parts"][0]["text"] == "sys"


def test_gemini_joins_multiple_parts(monkeypatch):
    from jobtrack.llm import GeminiLLM

    reply = {"candidates": [{"content": {"parts": [{"text": "AB"}, {"text": "CD"}]}}]}
    monkeypatch.setattr(llm_mod, "_request_json", lambda *a, **k: reply)

    assert (
        GeminiLLM(LLMConfig(provider="gemini", api_key="k")).complete("s", "u")
        == "ABCD"
    )


# --------------------------------------------------------------------------
# Live model discovery — hardcoded lists go stale, so ask the provider
# --------------------------------------------------------------------------
def test_openai_compatible_model_list_parses_the_standard_shape(monkeypatch):
    from jobtrack.llm import OpenAICompatLLM

    monkeypatch.setattr(
        llm_mod,
        "_request_json",
        lambda *a, **k: {"data": [{"id": "b-model"}, {"id": "a-model"}, {"nope": 1}]},
    )

    llm = OpenAICompatLLM(LLMConfig(provider="groq", api_key="k"))
    assert llm.available_models() == ["a-model", "b-model"]


def test_gemini_model_list_filters_out_non_text_models(monkeypatch):
    from jobtrack.llm import GeminiLLM

    monkeypatch.setattr(
        llm_mod,
        "_request_json",
        lambda *a, **k: {
            "models": [
                {
                    "name": "models/gemini-2.0-flash",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    )

    llm = GeminiLLM(LLMConfig(provider="gemini", api_key="k"))
    assert llm.available_models() == ["gemini-2.0-flash"]


def test_model_list_without_a_key_is_empty():
    from jobtrack.llm import build_llm

    assert build_llm(LLMConfig(provider="groq", api_key="")).available_models() == []


def test_model_list_survives_a_broken_provider(monkeypatch):
    from jobtrack.llm import OpenAICompatLLM

    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(llm_mod, "_request_json", boom)
    llm = OpenAICompatLLM(LLMConfig(provider="groq", api_key="k"))

    assert llm.available_models() == []


def test_groq_default_is_a_model_that_still_exists():
    """Llama 3.1 8B and 3.3 70B left Groq's free tier on 16 August 2026."""
    from jobtrack.llm import PROVIDER_DEFAULTS

    model = PROVIDER_DEFAULTS["groq"][1]
    assert "llama" not in model.lower()
    assert model == "openai/gpt-oss-120b"


def test_prompt_covers_incomplete_and_distinguishes_completed_steps():
    from jobtrack.llm import SYSTEM_PROMPT, VALID_EVENTS

    schema = SYSTEM_PROMPT.split("Use the actual application state")[0]
    assert all('"' + event + '"' in schema for event in VALID_EVENTS)
