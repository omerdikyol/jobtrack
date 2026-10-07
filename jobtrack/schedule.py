"""Install the daily sync as a launchd agent (macOS) or a cron job (Linux).

An unattended job fails differently from an interactive one: nobody sees the
error. So the generated job logs to a file, and `jobtrack schedule status`
reads that log plus the database to tell you whether the sync is actually
running — the usual reason it stops is an expired Google token.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from jobtrack.config import DATA_DIR, DB_PATH

LABEL = "com.jobtrack.sync"
CRON_MARKER = "# jobtrack-daily-sync"
DEFAULT_HOUR = 8
DEFAULT_MINUTE = 0
DEFAULT_SINCE_DAYS = 7


class ScheduleError(RuntimeError):
    """Raised when a schedule cannot be installed or removed."""


class UnsupportedPlatform(ScheduleError):
    def __init__(self, platform: str):
        super().__init__(
            f"Automatic scheduling is not supported on {platform!r}. "
            "Run `jobtrack token-check`-style verification by hand, or add a "
            "Windows Task Scheduler entry that runs:\n\n    "
            f"{sys.executable} -m jobtrack --db {DB_PATH} sync --no-auth --since 7\n"
        )
        self.platform = platform


@dataclass
class ScheduleSpec:
    hour: int = DEFAULT_HOUR
    minute: int = DEFAULT_MINUTE
    since: int = DEFAULT_SINCE_DAYS
    python: str = field(default_factory=lambda: sys.executable)
    db_path: str = field(default_factory=lambda: str(DB_PATH))
    cwd: str = field(default_factory=lambda: str(Path.cwd()))
    log_path: str = field(default_factory=lambda: str(DATA_DIR / "sync.log"))
    home_env: str | None = field(
        default_factory=lambda: os.environ.get("JOBTRACK_HOME")
    )

    @property
    def at(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    def command(self) -> list[str]:
        """The exact argv the scheduler will run."""
        return [
            self.python,
            "-m",
            "jobtrack",
            "--db",
            self.db_path,
            "sync",
            "--no-auth",
            "--since",
            str(self.since),
        ]

    def command_line(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command())


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _subprocess_run(args: list[str], input_text: str | None = None) -> Result:
    try:
        proc = subprocess.run(
            args, input=input_text, capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        return Result(127, "", f"{args[0]} is not installed")
    except subprocess.TimeoutExpired:
        return Result(124, "", f"{args[0]} timed out")
    return Result(proc.returncode, proc.stdout, proc.stderr)


def parse_at(value: str) -> tuple[int, int]:
    """Parse a 'HH:MM' time."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ScheduleError(f"Expected a time like 08:30, got {value!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ScheduleError(f"Expected a time like 08:30, got {value!r}") from None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleError(f"Time out of range: {value!r}")
    return hour, minute


def next_run(hour: int, minute: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def describe_next_run(hour: int, minute: int, now: datetime | None = None) -> str:
    """A daily schedule can only ever fire today or tomorrow."""
    now = now or datetime.now()
    upcoming = next_run(hour, minute, now)
    if upcoming.date() == now.date():
        return f"today at {upcoming:%H:%M}"
    return f"tomorrow at {upcoming:%H:%M}"


class Scheduler:
    """Interface shared by the launchd and cron implementations."""

    mechanism = "unknown"

    def __init__(self, spec: ScheduleSpec, run=None, home: Path | str | None = None):
        self.spec = spec
        self._run = run or _subprocess_run
        self.home = Path(home) if home else Path.home()

    def install(self) -> str:
        raise NotImplementedError

    def uninstall(self) -> str:
        raise NotImplementedError

    def is_installed(self) -> bool:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def installed_details(self) -> dict | None:
        """Read back the installed configuration, or None if there is none."""
        raise NotImplementedError


class LaunchdScheduler(Scheduler):
    """macOS. launchd also runs a missed job once the machine wakes up."""

    mechanism = "launchd"

    @property
    def plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def payload(self) -> dict:
        payload: dict = {
            "Label": LABEL,
            "ProgramArguments": self.spec.command(),
            "WorkingDirectory": self.spec.cwd,
            "StartCalendarInterval": {
                "Hour": self.spec.hour,
                "Minute": self.spec.minute,
            },
            "StandardOutPath": self.spec.log_path,
            "StandardErrorPath": self.spec.log_path,
            "RunAtLoad": False,
            "ProcessType": "Background",
        }
        if self.spec.home_env:
            # launchd starts with a bare environment, so JOBTRACK_HOME would be
            # lost and the job would read a different database.
            payload["EnvironmentVariables"] = {"JOBTRACK_HOME": self.spec.home_env}
        return payload

    def install(self) -> str:
        path = self.plist_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(self.payload()))

        domain = f"gui/{os.getuid()}"
        # Reinstalling over a loaded agent fails, so always drop it first.
        self._run(["launchctl", "bootout", f"{domain}/{LABEL}"])
        result = self._run(["launchctl", "bootstrap", domain, str(path)])
        if result.returncode != 0:
            result = self._run(["launchctl", "load", "-w", str(path)])
        if result.returncode != 0:
            raise ScheduleError(
                f"Wrote {path} but launchctl could not load it:\n{result.stderr.strip()}"
            )
        return f"launchd agent installed — runs daily at {self.spec.at}."

    def uninstall(self) -> str:
        path = self.plist_path
        domain = f"gui/{os.getuid()}"
        self._run(["launchctl", "bootout", f"{domain}/{LABEL}"])
        self._run(["launchctl", "unload", "-w", str(path)])
        if path.exists():
            path.unlink()
            return "Removed the daily sync (launchd agent and plist)."
        return "No jobtrack schedule was installed."

    def is_installed(self) -> bool:
        return self.plist_path.is_file()

    def describe(self) -> str:
        return f"launchd agent {LABEL} ({self.plist_path})"

    def installed_details(self) -> dict | None:
        if not self.plist_path.is_file():
            return None
        try:
            payload = plistlib.loads(self.plist_path.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            return None

        interval = payload.get("StartCalendarInterval") or {}
        args = [str(a) for a in payload.get("ProgramArguments") or []]
        raw = " ".join(shlex.quote(a) for a in args)
        return {
            "hour": interval.get("Hour"),
            "minute": interval.get("Minute"),
            "raw": raw,
            "since": _extract_flag(raw, "--since"),
            "db": _extract_flag(raw, "--db"),
            "log": payload.get("StandardOutPath"),
        }


class CronScheduler(Scheduler):
    """Linux. Missed runs are skipped if the machine was off."""

    mechanism = "cron"

    def line(self) -> str:
        prefix = ""
        if self.spec.home_env:
            prefix = f"JOBTRACK_HOME={shlex.quote(self.spec.home_env)} "
        return (
            f"{self.spec.minute} {self.spec.hour} * * * "
            f"cd {shlex.quote(self.spec.cwd)} && "
            f"{prefix}{self.spec.command_line()} "
            f">> {shlex.quote(self.spec.log_path)} 2>&1  {CRON_MARKER}"
        )

    def _existing(self) -> list[str]:
        result = self._run(["crontab", "-l"])
        if result.returncode != 0:
            return []  # no crontab yet
        return result.stdout.splitlines()

    def _without_ours(self, lines: list[str]) -> list[str]:
        return [line for line in lines if CRON_MARKER not in line]

    def _write(self, lines: list[str]) -> Result:
        return self._run(["crontab", "-"], input_text="\n".join(lines) + "\n")

    def install(self) -> str:
        kept = self._without_ours(self._existing())
        while kept and not kept[-1].strip():
            kept.pop()
        kept.append(self.line())

        result = self._write(kept)
        if result.returncode != 0:
            raise ScheduleError(
                f"Could not write the crontab: {result.stderr.strip() or result.stdout.strip()}"
            )
        return f"cron job installed — runs daily at {self.spec.at}."

    def uninstall(self) -> str:
        lines = self._existing()
        if not any(CRON_MARKER in line for line in lines):
            return "No jobtrack schedule was installed."
        result = self._write(self._without_ours(lines))
        if result.returncode != 0:
            raise ScheduleError(f"Could not write the crontab: {result.stderr.strip()}")
        return "Removed the daily sync from your crontab."

    def is_installed(self) -> bool:
        return any(CRON_MARKER in line for line in self._existing())

    def describe(self) -> str:
        return "cron job (crontab)"

    def installed_details(self) -> dict | None:
        for line in self._existing():
            if CRON_MARKER not in line:
                continue
            schedule = line.strip().split()[:2]
            hour = minute = None
            if len(schedule) == 2 and all(part.isdigit() for part in schedule):
                minute, hour = int(schedule[0]), int(schedule[1])
            raw = line.split(CRON_MARKER)[0].strip()
            return {
                "hour": hour,
                "minute": minute,
                "raw": raw,
                "since": _extract_flag(raw, "--since"),
                "db": _extract_flag(raw, "--db"),
                "log": _extract_flag(raw, ">>"),
            }
        return None


def _extract_flag(raw: str, flag: str) -> str | None:
    match = re.search(rf"{re.escape(flag)}\s+(\S+)", raw)
    return match.group(1).strip("'\"") if match else None


def get_scheduler(
    spec: ScheduleSpec,
    platform: str | None = None,
    run=None,
    home: Path | str | None = None,
) -> Scheduler:
    platform = platform or sys.platform
    if platform == "darwin":
        return LaunchdScheduler(spec, run=run, home=home)
    if platform.startswith("linux"):
        return CronScheduler(spec, run=run, home=home)
    raise UnsupportedPlatform(platform)
