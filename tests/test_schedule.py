import plistlib
from datetime import datetime

import pytest

from jobtrack.schedule import (
    CRON_MARKER,
    CronScheduler,
    LaunchdScheduler,
    Result,
    ScheduleError,
    ScheduleSpec,
    UnsupportedPlatform,
    describe_next_run,
    get_scheduler,
    next_run,
    parse_at,
)


def spec(**overrides):
    base = {
        "hour": 8,
        "minute": 30,
        "since": 7,
        "python": "/usr/bin/python3",
        "db_path": "/data/jobtrack.db",
        "cwd": "/code/job_track",
        "log_path": "/data/sync.log",
        "home_env": None,
    }
    base.update(overrides)
    return ScheduleSpec(**base)


class FakeRunner:
    """Records commands and replays canned results."""

    def __init__(self, results=None):
        self.calls = []
        self.inputs = []
        self.results = list(results or [])

    def __call__(self, args, input_text=None):
        self.calls.append(args)
        self.inputs.append(input_text)
        if self.results:
            return self.results.pop(0)
        return Result(0, "", "")


# --------------------------------------------------------------------------
# Time parsing and the next-run calculation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected", [("08:30", (8, 30)), ("0:05", (0, 5)), ("23:59", (23, 59))]
)
def test_parse_at(text, expected):
    assert parse_at(text) == expected


@pytest.mark.parametrize("text", ["8", "25:00", "12:60", "eight:30", "", "8:30:00"])
def test_parse_at_rejects_bad_input(text):
    with pytest.raises(ScheduleError):
        parse_at(text)


def test_next_run_is_today_when_the_time_is_still_ahead():
    now = datetime(2026, 10, 3, 7, 0)
    assert next_run(8, 30, now) == datetime(2026, 10, 3, 8, 30)


def test_next_run_rolls_to_tomorrow_once_the_time_has_passed():
    now = datetime(2026, 10, 3, 9, 0)
    assert next_run(8, 30, now) == datetime(2026, 10, 4, 8, 30)


def test_next_run_rolls_over_at_the_exact_minute():
    now = datetime(2026, 10, 3, 8, 30, 0)
    assert next_run(8, 30, now) == datetime(2026, 10, 4, 8, 30)


def test_describe_next_run():
    now = datetime(2026, 10, 3, 7, 0)
    assert describe_next_run(8, 30, now) == "today at 08:30"
    assert describe_next_run(6, 0, now) == "tomorrow at 06:00"
    # A daily schedule never fires further out than tomorrow.
    assert describe_next_run(8, 30, datetime(2026, 10, 9, 9, 0)) == "tomorrow at 08:30"


# --------------------------------------------------------------------------
# Spec
# --------------------------------------------------------------------------
def test_spec_builds_a_headless_sync_command():
    command = spec().command()

    assert command[1:3] == ["-m", "jobtrack"]
    assert "sync" in command
    assert "--no-auth" in command
    assert command[command.index("--since") + 1] == "7"
    assert command[command.index("--db") + 1] == "/data/jobtrack.db"


def test_spec_command_line_quotes_paths_with_spaces():
    line = spec(db_path="/my data/jobtrack.db").command_line()
    assert "'/my data/jobtrack.db'" in line


# --------------------------------------------------------------------------
# launchd
# --------------------------------------------------------------------------
def test_plist_is_valid_and_carries_the_schedule(tmp_path):
    runner = FakeRunner()
    scheduler = LaunchdScheduler(spec(), run=runner, home=tmp_path)

    scheduler.install()
    payload = plistlib.loads(scheduler.plist_path.read_bytes())

    assert payload["Label"] == "com.jobtrack.sync"
    assert payload["StartCalendarInterval"] == {"Hour": 8, "Minute": 30}
    assert payload["ProgramArguments"][1:3] == ["-m", "jobtrack"]
    assert payload["StandardErrorPath"] == "/data/sync.log"
    assert payload["RunAtLoad"] is False
    assert "EnvironmentVariables" not in payload


def test_plist_passes_jobtrack_home_through(tmp_path):
    # launchd starts with a bare environment; without this the job would read a
    # different database than the interactive CLI.
    runner = FakeRunner()
    scheduler = LaunchdScheduler(spec(home_env="/custom/home"), run=runner, home=tmp_path)

    scheduler.install()
    payload = plistlib.loads(scheduler.plist_path.read_bytes())

    assert payload["EnvironmentVariables"] == {"JOBTRACK_HOME": "/custom/home"}


def test_launchd_install_boots_out_before_bootstrapping(tmp_path):
    runner = FakeRunner()
    scheduler = LaunchdScheduler(spec(), run=runner, home=tmp_path)

    message = scheduler.install()

    assert runner.calls[0][:2] == ["launchctl", "bootout"]
    assert runner.calls[1][:2] == ["launchctl", "bootstrap"]
    assert "daily at 08:30" in message
    assert scheduler.is_installed()


def test_launchd_falls_back_to_load_when_bootstrap_fails(tmp_path):
    runner = FakeRunner([Result(0), Result(5, "", "Bootstrap failed"), Result(0)])
    scheduler = LaunchdScheduler(spec(), run=runner, home=tmp_path)

    scheduler.install()

    assert runner.calls[2][:3] == ["launchctl", "load", "-w"]


def test_launchd_reports_a_hard_failure(tmp_path):
    runner = FakeRunner([Result(0), Result(5, "", "nope"), Result(1, "", "also nope")])
    scheduler = LaunchdScheduler(spec(), run=runner, home=tmp_path)

    with pytest.raises(ScheduleError, match="could not load"):
        scheduler.install()


def test_launchd_uninstall_removes_the_plist(tmp_path):
    scheduler = LaunchdScheduler(spec(), run=FakeRunner(), home=tmp_path)
    scheduler.install()

    message = scheduler.uninstall()

    assert not scheduler.plist_path.exists()
    assert not scheduler.is_installed()
    assert "Removed" in message


def test_launchd_uninstall_when_nothing_is_installed(tmp_path):
    scheduler = LaunchdScheduler(spec(), run=FakeRunner(), home=tmp_path)
    assert "No jobtrack schedule" in scheduler.uninstall()


def test_launchd_reads_back_what_was_installed(tmp_path):
    scheduler = LaunchdScheduler(spec(hour=6, minute=15, since=14), run=FakeRunner(), home=tmp_path)
    scheduler.install()

    details = scheduler.installed_details()

    assert details["hour"] == 6
    assert details["minute"] == 15
    assert details["since"] == "14"
    assert details["log"] == "/data/sync.log"


def test_launchd_details_are_none_when_absent(tmp_path):
    scheduler = LaunchdScheduler(spec(), run=FakeRunner(), home=tmp_path)
    assert scheduler.installed_details() is None


# --------------------------------------------------------------------------
# cron
# --------------------------------------------------------------------------
def test_cron_line_is_well_formed():
    line = CronScheduler(spec(), run=FakeRunner()).line()

    assert line.startswith("30 8 * * * ")
    assert "/usr/bin/python3" in line
    assert "--no-auth" in line
    assert line.rstrip().endswith(CRON_MARKER)


def test_cron_line_exports_jobtrack_home():
    line = CronScheduler(spec(home_env="/custom/home"), run=FakeRunner()).line()
    assert "JOBTRACK_HOME=/custom/home" in line


def test_cron_install_preserves_existing_entries():
    existing = Result(0, "0 3 * * * /usr/bin/backup.sh\n# mine\n", "")
    runner = FakeRunner([existing, Result(0)])
    scheduler = CronScheduler(spec(), run=runner)

    scheduler.install()

    written = runner.inputs[1]
    assert "/usr/bin/backup.sh" in written
    assert "# mine" in written
    assert CRON_MARKER in written
    assert written.count(CRON_MARKER) == 1


def test_cron_install_replaces_a_previous_entry():
    old = f"0 8 * * * old-thing  {CRON_MARKER}\n"
    runner = FakeRunner([Result(0, old, ""), Result(0)])
    scheduler = CronScheduler(spec(), run=runner)

    scheduler.install()

    written = runner.inputs[1]
    assert "old-thing" not in written
    assert written.count(CRON_MARKER) == 1
    assert "30 8 * * *" in written


def test_cron_install_handles_a_missing_crontab():
    runner = FakeRunner([Result(1, "", "no crontab for user"), Result(0)])
    scheduler = CronScheduler(spec(), run=runner)

    scheduler.install()

    assert CRON_MARKER in runner.inputs[1]


def test_cron_install_reports_a_write_failure():
    runner = FakeRunner([Result(0, "", ""), Result(1, "", "permission denied")])
    scheduler = CronScheduler(spec(), run=runner)

    with pytest.raises(ScheduleError, match="permission denied"):
        scheduler.install()


def test_cron_uninstall_removes_only_our_line():
    content = f"0 3 * * * /usr/bin/backup.sh\n30 8 * * * python  {CRON_MARKER}\n"
    runner = FakeRunner([Result(0, content, ""), Result(0)])
    scheduler = CronScheduler(spec(), run=runner)

    message = scheduler.uninstall()

    written = runner.inputs[1]
    assert "/usr/bin/backup.sh" in written
    assert CRON_MARKER not in written
    assert "Removed" in message


def test_cron_uninstall_when_nothing_is_installed():
    runner = FakeRunner([Result(0, "0 3 * * * /usr/bin/backup.sh\n", "")])
    scheduler = CronScheduler(spec(), run=runner)

    assert "No jobtrack schedule" in scheduler.uninstall()
    assert len(runner.calls) == 1  # never rewrites the crontab


def test_cron_is_installed_detects_the_marker():
    runner = FakeRunner([Result(0, f"30 8 * * * x  {CRON_MARKER}\n", "")])
    assert CronScheduler(spec(), run=runner).is_installed() is True


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def test_factory_picks_the_right_backend():
    assert isinstance(get_scheduler(spec(), platform="darwin"), LaunchdScheduler)
    assert isinstance(get_scheduler(spec(), platform="linux"), CronScheduler)
    assert isinstance(get_scheduler(spec(), platform="linux2"), CronScheduler)


def test_factory_rejects_other_platforms():
    with pytest.raises(UnsupportedPlatform, match="win32"):
        get_scheduler(spec(), platform="win32")


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------
class FakeScheduler:
    mechanism = "fake"

    def __init__(self, details=None, message="done"):
        self._details = details
        self.message = message
        self.installed = False
        self.calls = []

    def install(self):
        self.calls.append("install")
        self.installed = True
        return self.message

    def uninstall(self):
        self.calls.append("uninstall")
        self.installed = False
        return self.message

    def is_installed(self):
        return self.installed

    def describe(self):
        return "fake scheduler"

    def installed_details(self):
        return self._details


def run_schedule(capsys, monkeypatch, scheduler, *argv, db_path):
    """Run the CLI and return (exit code, stdout and stderr combined)."""
    from jobtrack import cli as cli_mod
    from jobtrack import schedule as schedule_mod

    monkeypatch.setattr(schedule_mod, "get_scheduler", lambda *a, **k: scheduler)
    monkeypatch.setattr(cli_mod, "_token_health", lambda: (True, "valid"))
    code = cli_mod.main(["--db", str(db_path), "schedule", *argv])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_cli_install_reports_the_schedule(capsys, monkeypatch, tmp_path):
    scheduler = FakeScheduler(message="launchd agent installed — runs daily at 07:45.")
    code, out = run_schedule(
        capsys, monkeypatch, scheduler, "install", "--at", "07:45", db_path=tmp_path / "a.db"
    )

    assert code == 0
    assert scheduler.calls == ["install"]
    assert "07:45" in out


def test_cli_install_rejects_a_bad_time(capsys, monkeypatch, tmp_path):
    scheduler = FakeScheduler()
    code, out = run_schedule(
        capsys, monkeypatch, scheduler, "install", "--at", "25:00", db_path=tmp_path / "b.db"
    )

    assert code == 1
    assert scheduler.calls == []
    assert "out of range" in out


def test_cli_status_when_nothing_is_installed(capsys, monkeypatch, tmp_path):
    code, out = run_schedule(
        capsys, monkeypatch, FakeScheduler(), "status", db_path=tmp_path / "c.db"
    )

    assert code == 0
    assert "not installed" in out
    assert "schedule install" in out


def test_cli_status_reports_the_installed_schedule(capsys, monkeypatch, tmp_path):
    details = {
        "hour": 9,
        "minute": 15,
        "raw": "python -m jobtrack sync --no-auth --since 7",
        "since": "7",
        "db": str(tmp_path / "d.db"),
        "log": str(tmp_path / "sync.log"),
    }
    code, out = run_schedule(
        capsys, monkeypatch, FakeScheduler(details), "status", db_path=tmp_path / "d.db"
    )

    assert code == 0
    assert "daily at 09:15" in out
    assert "last 7 days of mail" in out


def test_cli_status_warns_when_the_token_is_dead(capsys, monkeypatch, tmp_path):
    from jobtrack import cli as cli_mod
    from jobtrack import schedule as schedule_mod

    details = {
        "hour": 8,
        "minute": 0,
        "raw": "sync",
        "since": "7",
        "db": None,
        "log": None,
    }
    monkeypatch.setattr(schedule_mod, "get_scheduler", lambda *a, **k: FakeScheduler(details))
    monkeypatch.setattr(cli_mod, "_token_health", lambda: (False, "token expired"))

    code = cli_mod.main(["--db", str(tmp_path / "e.db"), "schedule", "status"])
    out = capsys.readouterr().out

    assert code == 0
    assert "failed" in out
    assert "jobtrack auth" in out


def test_cli_status_warns_when_the_last_sync_is_stale(capsys, monkeypatch, tmp_path):
    from datetime import timedelta, timezone

    from jobtrack import cli as cli_mod
    from jobtrack import schedule as schedule_mod
    from jobtrack.store import Store

    db = tmp_path / "f.db"
    stale = datetime.now(timezone.utc) - timedelta(days=4)
    with Store(db) as store:
        store.set_meta("last_sync_at", stale.isoformat())

    details = {"hour": 8, "minute": 0, "raw": "sync", "since": "7", "db": None, "log": None}
    monkeypatch.setattr(schedule_mod, "get_scheduler", lambda *a, **k: FakeScheduler(details))
    monkeypatch.setattr(cli_mod, "_token_health", lambda: (True, "valid"))

    code = cli_mod.main(["--db", str(db), "schedule", "status"])
    out = capsys.readouterr().out

    assert code == 0
    assert "longer than expected" in out
    assert "4d" in out


def test_cli_status_can_skip_the_token_check(capsys, monkeypatch, tmp_path):
    scheduler = FakeScheduler()
    code, out = run_schedule(
        capsys, monkeypatch, scheduler, "status", "--skip-token-check", db_path=tmp_path / "g.db"
    )

    assert code == 0
    assert "not checked" in out


def test_cli_uninstall(capsys, monkeypatch, tmp_path):
    scheduler = FakeScheduler(message="Removed the daily sync.")
    code, out = run_schedule(
        capsys, monkeypatch, scheduler, "uninstall", db_path=tmp_path / "h.db"
    )

    assert code == 0
    assert scheduler.calls == ["uninstall"]
    assert "Removed" in out


def test_cli_schedule_defaults_to_status(capsys, monkeypatch, tmp_path):
    code, out = run_schedule(capsys, monkeypatch, FakeScheduler(), db_path=tmp_path / "i.db")

    assert code == 0
    assert "not installed" in out
