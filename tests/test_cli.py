import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobtrack import analyze, cli
from jobtrack.auth import AuthError
from jobtrack.models import Classification
from jobtrack.store import Store
from tests.helpers import make_message


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "cli.db"
    with Store(path) as store:
        store.record(
            make_message("Thank you for applying to Acme", message_id="m1"),
            Classification(kind="applied", score=10, company="Acme", role="Data Analyst"),
        )
        store.record(
            make_message("Update on your application", message_id="m2", thread_id="t2"),
            Classification(kind="rejected", score=10, company="Globex"),
        )
    return path


def run(capsys, *argv):
    code = cli.main(["--db", str(argv[0]), *argv[1:]])
    return code, capsys.readouterr().out


def test_list_shows_tracked_applications(capsys, db_path):
    code, out = run(capsys, db_path, "list")

    assert code == 0
    assert "Acme" in out
    assert "Globex" in out


def test_list_active_hides_rejections(capsys, db_path):
    code, out = run(capsys, db_path, "list", "--active")

    assert code == 0
    assert "Acme" in out
    assert "Globex" not in out


def test_list_json(capsys, db_path):
    code, out = run(capsys, db_path, "list", "--json")

    assert code == 0
    assert '"company": "Acme"' in out


def test_show_by_id(capsys, db_path):
    code, out = run(capsys, db_path, "show", "1")

    assert code == 0
    assert "Acme" in out


def test_show_unknown_returns_error(capsys, db_path):
    code, out = run(capsys, db_path, "show", "does-not-exist")
    assert code == 1


def test_stats(capsys, db_path):
    code, out = run(capsys, db_path, "stats")

    assert code == 0
    assert "Applications" in out


def test_export_csv(capsys, db_path):
    code, out = run(capsys, db_path, "export", "--format", "csv")

    assert code == 0
    assert out.splitlines()[0].startswith("id,company,role,status")
    assert "Acme" in out


def test_export_json_to_file(capsys, db_path, tmp_path):
    target = tmp_path / "out.json"
    code, out = run(capsys, db_path, "export", "--format", "json", "-o", str(target))

    assert code == 0
    assert target.is_file()
    assert '"company": "Acme"' in target.read_text()


def test_reset_requires_confirmation(capsys, db_path):
    code, out = run(capsys, db_path, "reset")

    assert code == 1
    assert db_path.is_file()


def test_reset_with_yes_deletes_database(capsys, db_path):
    code, out = run(capsys, db_path, "reset", "--yes")

    assert code == 0
    assert not db_path.exists()


def test_sync_without_credentials_exits_with_auth_error(capsys, db_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AuthError("no token")

    monkeypatch.setattr(cli, "get_credentials", boom)
    code = cli.main(["--db", str(db_path), "sync", "--no-auth"])

    assert code == 2


# --------------------------------------------------------------------------
# Tracking your own applications
# --------------------------------------------------------------------------
def SyncSummaryStub():
    from jobtrack.sync import SyncSummary

    return SyncSummary()


def test_saved_setting_is_read_even_with_the_flag_passed(tmp_path, monkeypatch):
    """Reading settings must not depend on `or` short-circuiting.

    Regression: the lookup was wrong but unreached when --track-sent was given.
    """
    reads = []

    monkeypatch.setattr(cli, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(cli, "GmailClient", lambda creds: type("C", (), {
        "profile_email": lambda self: "me@example.com",
        "iter_messages": lambda self, query, limit=0: iter(()),
    })())
    monkeypatch.setattr(cli, "run_sync", lambda *a, **k: SyncSummaryStub())
    monkeypatch.setattr(cli, "load_settings", lambda: reads.append(1) or SimpleNamespace(track_sent=False))

    cli.main([
        "--db", str(tmp_path / "s.db"), "sync", "--no-auth", "--llm-mode", "off",
        "--track-sent",
    ])

    assert reads, "saved settings were never consulted"


def sync_with_capture(tmp_path, monkeypatch, *extra, saved=False):
    """Run a sync against an empty mailbox, capturing run_sync's keyword args."""
    from jobtrack.sync import SyncSummary

    captured = {}

    def fake_run_sync(*args, **kwargs):
        captured.update(kwargs)
        return SyncSummary()

    class Client:
        def profile_email(self):
            return "me@example.com"

        def iter_messages(self, query, limit=0):
            return iter(())

    monkeypatch.setattr(cli, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(cli, "GmailClient", lambda creds: Client())
    monkeypatch.setattr(cli, "run_sync", fake_run_sync)
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: SimpleNamespace(track_sent=saved),
    )

    db = tmp_path / "sent.db"
    cli.main(["--db", str(db), "sync", "--no-auth", "--llm-mode", "off", *extra])
    return captured


def test_track_sent_flag_is_off_by_default(tmp_path, monkeypatch):
    captured = sync_with_capture(tmp_path, monkeypatch)

    assert captured["track_sent"] is False


def test_track_sent_flag_reaches_the_pipeline(tmp_path, monkeypatch):
    captured = sync_with_capture(tmp_path, monkeypatch, "--track-sent")

    assert captured["track_sent"] is True


def test_saved_setting_enables_sent_applications_without_the_flag(
    tmp_path, monkeypatch
):
    captured = sync_with_capture(tmp_path, monkeypatch, saved=True)

    assert captured["track_sent"] is True


# --------------------------------------------------------------------------
# LLM wiring
# --------------------------------------------------------------------------
class FakeOllama:
    usable = True
    explanation = "Ollama ready"
    reply = (
        '{"job_related": true, "event": "rejected", "company": "Initech", '
        '"role": "Backend Engineer", "confidence": 0.88}'
    )

    def __init__(self, config=None):
        self.config = config

    def check(self):
        return type(self).usable, type(self).explanation

    def complete(self, system, user):
        return type(self).reply


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    for name in ("JOBTRACK_LLM_MODE", "JOBTRACK_LLM_MODEL", "JOBTRACK_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_llm_check_reports_a_working_model(capsys, monkeypatch):
    monkeypatch.setattr(cli, "build_llm", lambda config=None: FakeOllama(config))

    code = cli.main(["llm-check"])
    out = capsys.readouterr().out

    assert code == 0
    assert "rejected" in out
    assert "Initech" in out


def test_llm_check_reports_a_broken_setup(capsys, monkeypatch):
    class Broken(FakeOllama):
        usable = False
        explanation = "ollama pull qwen2.5:3b"

    monkeypatch.setattr(cli, "build_llm", lambda config=None: Broken(config))

    code = cli.main(["llm-check"])

    assert code == 2


def test_llm_mode_off_builds_a_rules_only_analyzer():
    args = cli.build_parser().parse_args(["sync", "--llm-mode", "off"])
    analyzer = cli._build_analyzer(args)

    assert analyzer.mode == "off"
    assert analyzer.llm is None


def test_auto_mode_falls_back_to_rules_when_the_model_is_missing(monkeypatch):
    class Missing(FakeOllama):
        usable = False
        explanation = "Cannot reach the model"

    monkeypatch.setattr(analyze, "build_llm", lambda config=None: Missing(config))

    args = cli.build_parser().parse_args(["sync"])
    analyzer = cli._build_analyzer(args)

    assert analyzer.mode == "off"
    assert analyzer.llm_active is False


def test_auto_mode_upgrades_to_fallback_when_the_model_is_ready(monkeypatch):
    monkeypatch.setattr(analyze, "build_llm", lambda config=None: FakeOllama(config))

    args = cli.build_parser().parse_args(["sync"])
    analyzer = cli._build_analyzer(args)

    assert analyzer.mode == "fallback"
    assert analyzer.llm_active is True


def test_explicit_mode_is_not_probed(monkeypatch):
    class Exploding(FakeOllama):
        def check(self):
            raise AssertionError("check() should not be called for an explicit mode")

    monkeypatch.setattr(analyze, "build_llm", lambda config=None: Exploding(config))

    args = cli.build_parser().parse_args(["sync", "--llm-mode", "always"])
    analyzer = cli._build_analyzer(args)

    assert analyzer.mode == "always"


def test_llm_env_default_is_respected(monkeypatch):
    monkeypatch.setenv("JOBTRACK_LLM_MODE", "off")
    monkeypatch.setattr(analyze, "build_llm", lambda config=None: FakeOllama(config))

    args = cli.build_parser().parse_args(["sync"])
    analyzer = cli._build_analyzer(args)

    assert analyzer.mode == "off"


def test_unknown_llm_mode_degrades_to_rules(monkeypatch):
    monkeypatch.setenv("JOBTRACK_LLM_MODE", "banana")
    monkeypatch.setattr(analyze, "build_llm", lambda config=None: FakeOllama(config))

    args = cli.build_parser().parse_args(["sync"])
    analyzer = cli._build_analyzer(args)

    assert analyzer.mode == "off"


def test_llm_flags_flow_into_the_config():
    args = cli.build_parser().parse_args(
        ["sync", "--llm-model", "llama3.2", "--llm-url", "http://box:11434/", "--llm-max-chars", "99"]
    )
    config = cli._llm_config(args)

    assert config.model == "llama3.2"
    assert config.base_url == "http://box:11434"
    assert config.max_chars == 99


# --------------------------------------------------------------------------
# Documentation references
# --------------------------------------------------------------------------
def test_error_messages_point_at_readme_sections_that_exist():
    """A deleted heading must not leave users chasing it.

    The token-expiry error used to name a 'Making the token last' section that
    no longer exists, and it is the message a new user is most likely to hit.
    """
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    headings = {
        line.lstrip("# ").strip()
        for line in readme.splitlines()
        if line.startswith("#")
    }

    referenced = set()
    for module in ("auth.py", "cli.py", "jobs.py", "sync.py", "schedule.py"):
        source = (Path(__file__).resolve().parents[1] / "jobtrack" / module).read_text(
            encoding="utf-8"
        )
        referenced.update(re.findall(r"section ['\"]+([A-Z][A-Za-z ]+?)['\"]?\.?\s*$", source, re.M))
        referenced.update(re.findall(r"section\s*\n?[\"']+([A-Z][A-Za-z ]+?)[\"']", source))

    for name in referenced:
        assert name in headings, f"README has no section named {name!r}"
