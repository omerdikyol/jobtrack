import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jobtrack import web as web_mod
from jobtrack.auth import AuthError
from jobtrack.models import Classification
from jobtrack.store import Store
from jobtrack.sync import SyncSummary
from tests.helpers import make_message

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "web.db"
    with Store(path) as store:
        store.record(
            make_message(
                "Thank you for applying to Stripe",
                "We received your application for the Backend Engineer role.",
                sender="Greenhouse <no-reply@greenhouse.io>",
                message_id="m1",
                thread_id="thread-stripe",
                date=BASE,
            ),
            Classification(kind="applied", score=12, company="Stripe", role="Backend Engineer"),
        )
        store.record(
            make_message(
                "Next steps for your application",
                "Let's schedule a call about the Backend Engineer role.",
                sender="Stripe Recruiting <recruiting@stripe.com>",
                message_id="m2",
                thread_id="thread-stripe",
                date=BASE + timedelta(days=4),
            ),
            Classification(kind="interview", score=14, company="Stripe"),
        )
        store.record(
            make_message(
                "Update on your application to Globex",
                "Unfortunately we have decided to move forward with other candidates.",
                message_id="m3",
                thread_id="thread-globex",
                date=BASE + timedelta(days=10),
            ),
            Classification(kind="rejected", score=11, company="Globex"),
        )
        store.set_meta("last_sync_at", "2026-09-05T10:00:00+00:00")
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    # Do not create the real user data directory during tests.
    monkeypatch.setattr(web_mod, "ensure_data_dir", lambda: None)
    return TestClient(web_mod.create_app(db_path))


# --------------------------------------------------------------------------
# Pages and read endpoints
# --------------------------------------------------------------------------
def test_index_serves_the_ui(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Job Tracker" in response.text
    assert "text/html" in response.headers["content-type"]


def test_list_applications(client):
    response = client.get("/api/applications")
    body = response.json()

    assert response.status_code == 200
    companies = [a["company"] for a in body["applications"]]
    assert companies == ["Globex", "Stripe"]  # newest activity first
    stripe = body["applications"][1]
    assert stripe["status_label"] == "Interviewing"
    assert stripe["event_count"] == 2


def test_list_filters_by_status(client):
    body = client.get("/api/applications?status=rejected").json()
    assert [a["company"] for a in body["applications"]] == ["Globex"]


def test_list_active_hides_rejections(client):
    body = client.get("/api/applications?active=true").json()
    assert [a["company"] for a in body["applications"]] == ["Stripe"]


def test_list_searches_company_and_role(client):
    assert len(client.get("/api/applications?search=stripe").json()["applications"]) == 1
    assert len(client.get("/api/applications?search=backend").json()["applications"]) == 1
    assert client.get("/api/applications?search=nothing").json()["applications"] == []


def test_detail_returns_a_timeline_with_gmail_links(client):
    body = client.get("/api/applications/1").json()
    events = body["events"]

    assert body["application"]["company"] == "Stripe"
    assert [e["kind"] for e in events] == ["applied", "interview"]
    assert events[0]["date"] < events[1]["date"]

    link = events[0]["gmail_url"]
    assert link == "https://mail.google.com/mail/u/0/#all/thread-stripe"
    assert events[0]["source"] == "rules"
    assert events[0]["label"] == "Applied"


def test_detail_404_for_unknown_application(client):
    assert client.get("/api/applications/9999").status_code == 404


def test_stats(client):
    body = client.get("/api/stats").json()

    assert body["statuses"] == {"interview": 1, "rejected": 1}
    assert body["events"] == 3
    assert body["applications"] == 2
    assert body["last_sync"] == "2026-09-05T10:00:00+00:00"
    assert body["labels"]["rejected"] == "Rejected"


# --------------------------------------------------------------------------
# Gmail deep links
# --------------------------------------------------------------------------
def test_gmail_url_for_a_thread():
    from jobtrack.models import gmail_thread_url

    assert gmail_thread_url("abc123") == "https://mail.google.com/mail/u/0/#all/abc123"


@pytest.mark.parametrize("thread_id", [None, "", "has spaces", "with/slash", "x" * 200])
def test_gmail_url_refuses_values_that_are_not_thread_ids(thread_id):
    from jobtrack.models import gmail_thread_url

    assert gmail_thread_url(thread_id) is None


# --------------------------------------------------------------------------
# Sync runner
# --------------------------------------------------------------------------
class FakeGmail:
    """Minimal stand-in for GmailClient."""

    def __init__(self, address="me@example.com"):
        self.address = address

    def profile_email(self):
        return self.address


def make_runner(db_path, monkeypatch):
    runner = web_mod.SyncRunner(str(db_path))
    monkeypatch.setattr(web_mod, "ensure_data_dir", lambda: None)
    return runner


def test_sync_runner_skips_mail_the_user_sent_themselves(db_path, monkeypatch):
    """Your own outgoing mail is not a response, and the model would guess badly."""
    captured = {"seen": []}

    class Client:
        def profile_email(self):
            return "me@example.com"

        def iter_messages(self, query, limit=0):
            yield make_message(
                "Application for Junior Engineer", sender="Me <me@example.com>", message_id="own"
            )
            yield make_message(
                "Thanks for applying to Acme",
                sender="Acme <jobs@acme.com>",
                message_id="theirs",
            )

    def fake_run_sync(*args, own_address=None, **kwargs):
        captured["own_address"] = own_address
        return SyncSummary()

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: Client())
    monkeypatch.setattr(web_mod, "run_sync", fake_run_sync)

    make_runner(db_path, monkeypatch)._run(web_mod.SyncRequest())

    assert captured["own_address"] == "me@example.com"


def test_sync_request_can_ask_for_sent_applications(db_path, monkeypatch):
    captured = {"track_sent": None, "since": None}

    class Client:
        def profile_email(self):
            return "me@example.com"

        def iter_messages(self, query, limit=0):
            return iter(())

    def fake_run_sync(*args, **kwargs):
        captured["track_sent"] = kwargs.get("track_sent")
        captured["since"] = kwargs.get("since")
        return SyncSummary()

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: Client())
    monkeypatch.setattr(web_mod, "run_sync", fake_run_sync)

    request = web_mod.SyncRequest(since="0", track_sent=True)
    make_runner(db_path, monkeypatch)._run(request)

    assert captured["track_sent"] is True
    assert captured["since"] == "0"

    make_runner(db_path, monkeypatch)._run(web_mod.SyncRequest(since="0"))
    assert captured["track_sent"] is False


def test_run_sync_drops_your_own_mail():
    """End to end through run_sync, without mocking it away."""
    from jobtrack.analyze import Analyzer
    from jobtrack.store import Store
    from jobtrack.sync import run_sync

    class Client:
        def iter_messages(self, query, limit=0):
            yield make_message("My own application", sender="Me <me@example.com>", message_id="a")
            yield make_message(
                "Thanks for applying to Acme",
                sender="Acme <jobs@acme.com>",
                message_id="b",
            )

    with Store(":memory:") as store:
        summary = run_sync(
            store,
            Client(),
            Analyzer(None, mode="off"),
            query="x",
            own_address="Me@Example.com",  # case should not matter
        )
        companies = [a.company for a in store.list_applications()]

    assert summary.skipped == 1
    assert summary.created == 1
    assert companies == ["Acme"]


def test_sync_runner_records_a_successful_run(db_path, monkeypatch):
    captured = {}

    def fake_run_sync(store, client, analyzer, **kwargs):
        captured.update(kwargs)
        return SyncSummary(scanned=4, created=2, matched=1, duplicate=1, applications=2)

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())
    monkeypatch.setattr(web_mod, "run_sync", fake_run_sync)

    runner = make_runner(db_path, monkeypatch)
    runner._run(web_mod.SyncRequest())

    state = runner.snapshot()
    assert state["status"] == "done"
    assert state["summary"]["created"] == 2
    assert state["scanned"] == 4
    assert "newer_than:365d" in captured["query"]
    assert captured["recheck"] is False


def test_sync_runner_honours_recheck_and_limit(db_path, monkeypatch):
    captured = {}

    def fake_run_sync(store, client, analyzer, **kwargs):
        captured.update(kwargs)
        return SyncSummary()

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())
    monkeypatch.setattr(web_mod, "run_sync", fake_run_sync)

    runner = make_runner(db_path, monkeypatch)
    runner._run(web_mod.SyncRequest(recheck=True, limit=50, since="0"))

    assert captured["recheck"] is True
    assert captured["limit"] == 50
    assert "newer_than" not in captured["query"]


def test_sync_runner_accepts_an_absolute_date_range(db_path, monkeypatch):
    captured = {}

    def fake_run_sync(store, client, analyzer, **kwargs):
        captured.update(kwargs)
        return SyncSummary()

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())
    monkeypatch.setattr(web_mod, "run_sync", fake_run_sync)

    runner = make_runner(db_path, monkeypatch)
    runner._run(
        web_mod.SyncRequest(since="2023-01-01", before="2024-06-01")
    )

    assert "after:2023/01/01" in captured["query"]
    assert "before:2024/06/01" in captured["query"]


def test_sync_runner_reports_an_unreadable_date(db_path, monkeypatch):
    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())

    runner = make_runner(db_path, monkeypatch)
    runner._run(web_mod.SyncRequest(since="last tuesday"))

    state = runner.snapshot()
    assert state["status"] == "error"
    assert "date" in state["error"]


# --------------------------------------------------------------------------
# Replaying history
# --------------------------------------------------------------------------
def test_list_as_of_rewinds_the_status(client):
    body = client.get("/api/applications?as_of=2026-09-02").json()

    assert body["as_of"] == "2026-09-02"
    # Globex had not written yet, and Stripe was still just "applied".
    assert [a["company"] for a in body["applications"]] == ["Stripe"]
    assert body["applications"][0]["status"] == "applied"
    assert body["applications"][0]["event_count"] == 1


def test_list_as_of_after_everything_matches_the_present(client):
    as_of = client.get("/api/applications?as_of=2026-09-12").json()
    now = client.get("/api/applications").json()

    assert [a["company"] for a in as_of["applications"]] == [
        a["company"] for a in now["applications"]
    ]
    assert [a["status"] for a in as_of["applications"]] == [
        a["status"] for a in now["applications"]
    ]


def test_detail_as_of_hides_later_events(client):
    body = client.get("/api/applications/1?as_of=2026-09-02").json()

    assert [e["kind"] for e in body["events"]] == ["applied"]
    assert body["application"]["status"] == "applied"
    assert body["as_of"] == "2026-09-02"


def test_stats_as_of(client):
    body = client.get("/api/stats?as_of=2026-09-02").json()

    assert body["statuses"] == {"applied": 1}
    assert body["events"] == 1
    assert body["applications"] == 1


def test_as_of_rejects_a_bad_date(client):
    response = client.get("/api/applications?as_of=not-a-date")

    assert response.status_code == 400
    assert "date" in response.json()["detail"]


def test_as_of_from_a_year_before_returns_nothing(client):
    body = client.get("/api/applications?as_of=2020-01-01").json()
    assert body["applications"] == []


def test_sync_runner_reports_an_auth_failure(db_path, monkeypatch):
    def boom(**kwargs):
        raise AuthError("Stored Gmail token is missing or expired.")

    monkeypatch.setattr(web_mod, "get_credentials", boom)

    runner = make_runner(db_path, monkeypatch)
    runner._run(web_mod.SyncRequest())

    state = runner.snapshot()
    assert state["status"] == "error"
    assert "expired" in state["error"]


def test_sync_runner_reports_an_unexpected_failure(db_path, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("gmail exploded")

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())
    monkeypatch.setattr(web_mod, "run_sync", boom)

    runner = make_runner(db_path, monkeypatch)
    runner._run(web_mod.SyncRequest())

    state = runner.snapshot()
    assert state["status"] == "error"
    assert "ValueError" in state["error"]
    assert "gmail exploded" in state["error"]


def test_sync_runner_only_allows_one_run_at_a_time(db_path, monkeypatch):
    release = threading.Event()

    def blocking_run_sync(*args, **kwargs):
        release.wait(timeout=5)
        return SyncSummary()

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())
    monkeypatch.setattr(web_mod, "run_sync", blocking_run_sync)

    runner = make_runner(db_path, monkeypatch)
    try:
        assert runner.start(web_mod.SyncRequest()) is True
        assert runner.start(web_mod.SyncRequest()) is False  # already running
        assert runner.snapshot()["status"] == "running"
    finally:
        release.set()


def test_sync_endpoint_starts_a_job_and_reports_conflicts(db_path, monkeypatch):
    monkeypatch.setattr(web_mod, "ensure_data_dir", lambda: None)
    release = threading.Event()

    def blocking_run_sync(*args, **kwargs):
        release.wait(timeout=5)
        return SyncSummary(scanned=1, created=1)

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())
    monkeypatch.setattr(web_mod, "run_sync", blocking_run_sync)

    client = TestClient(web_mod.create_app(db_path))
    try:
        first = client.post("/api/sync", json={"recheck": False})
        assert first.status_code == 200
        assert first.json()["status"] == "running"

        conflict = client.post("/api/sync", json={})
        assert conflict.status_code == 409
        assert "already running" in conflict.json()["detail"]
    finally:
        release.set()


def test_sync_request_validates_input():
    with pytest.raises(Exception):
        web_mod.SyncRequest(since=-5)
    with pytest.raises(Exception):
        web_mod.SyncRequest(since=99999)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def test_settings_defaults(client):
    body = client.get("/api/settings").json()

    assert body["llm_provider"] == "ollama"
    assert body["llm_mode"] == "auto"
    assert "gemini" in body["providers"]
    assert "groq" in body["providers"]
    assert body["provider_defaults"]["groq"]["key_env"] == "GROQ_API_KEY"
    assert body["effective"]["api_key_set"] is False


def test_saving_a_provider_and_key(client):
    client.put("/api/settings", json={"llm_provider": "groq", "api_key": "gsk_secret_value"})
    body = client.get("/api/settings").json()

    assert body["llm_provider"] == "groq"
    assert body["effective"]["provider"] == "groq"
    assert body["effective"]["model"] == "openai/gpt-oss-120b"  # provider default
    assert body["effective"]["api_key_set"] is True
    assert body["effective"]["api_key_source"] == "settings"
    assert "groq" in body["providers_with_keys"]


def test_the_raw_key_is_never_sent_back(client):
    secret = "gsk_super_secret_do_not_leak_1234"
    stored = client.put("/api/settings", json={"llm_provider": "groq", "api_key": secret})
    fetched = client.get("/api/settings")

    for response in (stored, fetched):
        assert secret not in response.text
        assert "do_not_leak" not in response.text
    # Only a recognisable stub is exposed.
    assert fetched.json()["effective"]["api_key_hint"].endswith("1234")
    assert fetched.json()["effective"]["api_key_hint"].startswith("gsk_")


def test_settings_are_written_to_a_private_file(client, tmp_path):
    from jobtrack import settings as settings_mod

    client.put("/api/settings", json={"llm_provider": "gemini", "api_key": "g-key"})

    path = settings_mod.SETTINGS_PATH
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert "g-key" in path.read_text()


def test_omitting_the_key_leaves_it_alone(client):
    client.put("/api/settings", json={"llm_provider": "groq", "api_key": "keep-me"})
    client.put("/api/settings", json={"llm_mode": "always"})

    body = client.get("/api/settings").json()
    assert body["effective"]["api_key_set"] is True
    assert body["llm_mode"] == "always"


def test_an_empty_key_clears_it(client):
    client.put("/api/settings", json={"llm_provider": "groq", "api_key": "throw-away"})
    client.put("/api/settings", json={"api_key": ""})

    body = client.get("/api/settings").json()
    assert body["effective"]["api_key_set"] is False
    assert body["providers_with_keys"] == []


def test_keys_are_kept_per_provider(client):
    client.put("/api/settings", json={"llm_provider": "groq", "api_key": "groq-key"})
    client.put("/api/settings", json={"llm_provider": "gemini", "api_key": "gemini-key"})
    client.put("/api/settings", json={"llm_provider": "groq"})

    body = client.get("/api/settings").json()
    assert body["effective"]["api_key_set"] is True
    assert set(body["providers_with_keys"]) == {"groq", "gemini"}


def test_clearing_a_field_falls_back_to_the_default(client):
    client.put("/api/settings", json={"llm_model": "my-model"})
    assert client.get("/api/settings").json()["effective"]["model"] == "my-model"

    client.put("/api/settings", json={"llm_model": ""})
    assert client.get("/api/settings").json()["effective"]["model"] == "qwen2.5:3b"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"llm_provider": "nonsense"}, "Unknown provider"),
        ({"llm_mode": "sometimes"}, "Unknown mode"),
    ],
)
def test_invalid_settings_are_rejected(client, payload, message):
    response = client.put("/api/settings", json=payload)

    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_lookback_is_saved(client):
    client.put("/api/settings", json={"sync_since": "90"})
    assert client.get("/api/settings").json()["sync_since"] == "90"


class FakeLLM:
    max_chars = 4000

    def __init__(self, usable=True, reply=None, error=None):
        self.usable = usable
        self.reply = reply or (
            '{"job_related": true, "event": "rejected", "company": "Initech", '
            '"role": null, "confidence": 0.88}'
        )
        self.error = error

    def check(self):
        return (self.usable, "ready" if self.usable else "no key")

    def complete(self, system, user):
        if self.error:
            raise self.error
        return self.reply


def test_test_endpoint_classifies_the_sample(client, monkeypatch):
    monkeypatch.setattr(web_mod, "build_llm", lambda config=None: FakeLLM())

    body = client.post("/api/settings/test").json()

    assert body["ok"] is True
    assert body["sample"]["event"] == "rejected"
    assert body["sample"]["company"] == "Initech"
    assert body["sample"]["confidence"] == 0.88


def test_test_endpoint_reports_a_missing_key(client, monkeypatch):
    monkeypatch.setattr(web_mod, "build_llm", lambda config=None: FakeLLM(usable=False))

    body = client.post("/api/settings/test").json()

    assert body["ok"] is False
    assert "no key" in body["error"]


def test_test_endpoint_reports_a_model_failure(client, monkeypatch):
    from jobtrack.llm import LLMError

    monkeypatch.setattr(
        web_mod, "build_llm", lambda config=None: FakeLLM(error=LLMError("quota exhausted"))
    )

    body = client.post("/api/settings/test").json()

    assert body["ok"] is False
    assert "quota exhausted" in body["error"]


def test_test_endpoint_handles_an_unreadable_reply(client, monkeypatch):
    monkeypatch.setattr(
        web_mod, "build_llm", lambda config=None: FakeLLM(reply="I cannot help with that.")
    )

    body = client.post("/api/settings/test").json()

    assert body["ok"] is False
    assert "no JSON" in body["error"] or "JSON" in body["error"]


def test_model_list_endpoint_asks_the_provider(client, monkeypatch):
    """Hardcoded model lists rot, so the UI asks the provider at runtime."""
    monkeypatch.setattr(
        web_mod, "available_models", lambda config: ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    )

    body = client.get("/api/settings/models?provider=groq").json()

    assert body["provider"] == "groq"
    assert "openai/gpt-oss-120b" in body["models"]


def test_model_list_defaults_to_the_configured_provider(client, monkeypatch):
    monkeypatch.setattr(web_mod, "available_models", lambda config: ["qwen2.5:3b"])

    body = client.get("/api/settings/models").json()

    assert body["provider"] == "ollama"


def test_model_list_rejects_an_unknown_provider(client):
    assert client.get("/api/settings/models?provider=nope").status_code == 400


def test_model_list_is_empty_rather_than_broken_when_unreachable(client, monkeypatch):
    monkeypatch.setattr(web_mod, "available_models", lambda config: [])

    body = client.get("/api/settings/models?provider=groq").json()

    assert body["models"] == []


def test_available_models_never_raises_on_a_bad_provider():
    from jobtrack.llm import LLMConfig, available_models

    assert available_models(LLMConfig(provider="nonsense")) == []


def test_settings_use_the_saved_provider_for_the_next_sync(client, db_path, monkeypatch):
    """A provider and mode saved in the UI must be what a sync actually uses."""
    captured = {}

    def fake_run_sync(store, client_, analyzer, **kwargs):
        captured["analyzer"] = analyzer
        return SyncSummary()

    monkeypatch.setattr(web_mod, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(web_mod, "GmailClient", lambda creds: object())
    monkeypatch.setattr(web_mod, "run_sync", fake_run_sync)

    client.put(
        "/api/settings",
        json={"llm_provider": "groq", "api_key": "gsk_test", "llm_mode": "always"},
    )

    runner = web_mod.SyncRunner(str(db_path))
    runner._run(web_mod.SyncRequest())

    analyzer = captured["analyzer"]
    assert analyzer.mode == "always"
    assert analyzer.llm.provider == "groq"
    assert analyzer.llm.config.model == "openai/gpt-oss-120b"
