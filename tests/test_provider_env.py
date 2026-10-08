import json
from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from jobtrack import llm, settings
from jobtrack.gmail_client import GmailClient


def test_env_keys_reload_and_do_not_export(monkeypatch):
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    settings.ENV_PATH.write_text(
        "NVIDIA_NIM_API_KEY='literal${UNSET}#key'\nGEMINI_API_KEY=google-key\n"
    )
    config = llm.LLMConfig.for_provider("nvidia")
    assert config.api_key == "literal${UNSET}#key"
    assert (
        settings.key_source(settings.Settings(), "nvidia", "NVIDIA_NIM_API_KEY")
        == ".env"
    )
    assert llm.LLMConfig.for_provider("gemini").api_key == "google-key"
    import os

    assert "NVIDIA_NIM_API_KEY" not in os.environ
    settings.ENV_PATH.write_text("NVIDIA_NIM_API_KEY=replaced\n")
    assert llm.LLMConfig.for_provider("nvidia").api_key == "replaced"


def test_saved_then_process_then_dotenv_precedence(monkeypatch):
    settings.ENV_PATH.write_text("NVIDIA_NIM_API_KEY=file-key\n")
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "process-key")
    assert llm.LLMConfig.for_provider("nvidia").api_key == "process-key"
    settings.save(settings.Settings(llm_api_keys={"nvidia": "saved-key"}))
    assert llm.LLMConfig.for_provider("nvidia").api_key == "saved-key"
    assert (
        settings.key_source(settings.load(), "nvidia", "NVIDIA_NIM_API_KEY")
        == "settings"
    )


def test_nim_actual_request_is_bounded(monkeypatch):
    captured = {}

    def fake(url, data, timeout, headers):
        captured.update(url=url, data=data, headers=headers)
        return {"choices": [{"message": {"content": '{"event":"applied"}'}}]}

    monkeypatch.setattr(llm, "_request_json", fake)
    client = llm.build_llm(llm.LLMConfig(provider="nvidia", api_key="test-key"))
    assert client.complete("system", "sample") == '{"event":"applied"}'
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["data"]["stream"] is False
    assert captured["data"]["max_tokens"] == 1024
    assert captured["data"]["chat_template_kwargs"]["enable_thinking"] is False


def test_openrouter_discovery_filters_paid_and_nontext(monkeypatch):
    def row(model, price="0", outputs=None):
        return {
            "id": model,
            "pricing": {"prompt": price, "completion": price},
            "architecture": {"output_modalities": outputs or ["text"]},
        }

    monkeypatch.setattr(
        llm,
        "_request_json",
        lambda *a, **k: {
            "data": [
                row("a:free"),
                row("b:free", "0.01"),
                row("paid"),
                row("music:free", outputs=["audio"]),
                row("openrouter/free"),
                {"id": "unknown:free"},
            ]
        },
    )
    client = llm.build_llm(llm.LLMConfig(provider="openrouter", api_key="test-key"))
    assert client.available_models() == ["a:free", "openrouter/free"]


@pytest.mark.parametrize(
    "model",
    [
        "nvidia/embed-qa-4",
        "nvidia/nemotron-parse",
        "meta/llama-guard-4-12b",
        "gemini-3.8-flash-tts",
        "google/lyria-3-pro-preview",
    ],
)
def test_non_review_models_are_excluded(model):
    assert not llm._review_model({"id": model}, "nvidia")


@pytest.mark.parametrize(
    "reason, retry",
    [
        ("rateLimitExceeded", True),
        ("userRateLimitExceeded", True),
        ("forbidden", False),
    ],
)
def test_gmail_retries_quota_but_not_permission_errors(monkeypatch, reason, retry):
    error = HttpError(
        httplib2.Response({"status": "403"}),
        json.dumps({"error": {"errors": [{"reason": reason}]}}).encode(),
    )
    request = Mock()
    request.execute.side_effect = [error, {"ok": True}]
    sleeps = []
    monkeypatch.setattr("jobtrack.gmail_client.time.sleep", sleeps.append)
    client = object.__new__(GmailClient)
    if retry:
        assert client._retry(request) == {"ok": True}
        assert sleeps == [15.0]
    else:
        with pytest.raises(HttpError):
            client._retry(request)
        assert not sleeps
