import base64
import secrets

from fastapi.testclient import TestClient

from jobtrack.web import create_app


def test_password_gate(monkeypatch, tmp_path):
    good = secrets.token_hex(8)
    monkeypatch.setenv("JOBTRACK_PASSWORD", good)
    client = TestClient(create_app(tmp_path / "t.db"))
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/applications").status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"u:nope").decode()}
    assert client.get("/api/applications", headers=bad).status_code == 401
    ok = {"Authorization": "Basic " + base64.b64encode(f"u:{good}".encode()).decode()}
    assert client.get("/api/applications", headers=ok).status_code == 200


def test_no_password_is_open(monkeypatch, tmp_path):
    monkeypatch.delenv("JOBTRACK_PASSWORD", raising=False)
    client = TestClient(create_app(tmp_path / "t.db"))
    assert client.get("/api/applications").status_code == 200


def test_oauth_start_without_client_is_clean_error(monkeypatch, tmp_path):
    monkeypatch.delenv("JOBTRACK_PASSWORD", raising=False)
    monkeypatch.setattr(
        "jobtrack.web.resolve_credentials", lambda _=None: tmp_path / "missing.json"
    )
    client = TestClient(create_app(tmp_path / "t.db"), follow_redirects=False)
    assert client.get("/oauth/start").status_code == 400
    assert client.get("/oauth/callback?state=x").status_code == 400


def test_sync_scheduler_thread_starts_only_when_enabled(monkeypatch, tmp_path):
    import threading

    monkeypatch.delenv("JOBTRACK_SYNC_INTERVAL_MIN", raising=False)
    before = threading.active_count()
    create_app(tmp_path / "a.db")
    assert threading.active_count() == before
    monkeypatch.setenv("JOBTRACK_SYNC_INTERVAL_MIN", "60")
    create_app(tmp_path / "b.db")
    assert threading.active_count() == before + 1
