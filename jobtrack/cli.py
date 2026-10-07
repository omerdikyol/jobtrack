"""Command line interface for jobtrack."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jobtrack import __version__
from jobtrack.analyze import MODES, Analyzer, build_analyzer
from jobtrack.auth import AuthError, get_credentials, revoke
from jobtrack.config import (
    DATA_DIR,
    DB_PATH,
    TOKEN_PATH,
    WEB_HOST,
    WEB_PORT,
    ensure_data_dir,
    resolve_credentials,
)
from jobtrack.dashboard import (
    print_application,
    print_applications,
    print_stats,
    print_sync_summary,
)
from jobtrack.gmail_client import GmailClient, default_query
from jobtrack.llm import (
    PROVIDER_DEFAULTS,
    SAMPLE_EMAIL,
    SYSTEM_PROMPT,
    LLMConfig,
    LLMError,
    build_llm,
    extract_json,
    interpret,
    render_email,
)
from jobtrack.models import ACTIVE_STATUSES
from jobtrack.settings import load as load_settings
from jobtrack.store import Store
from jobtrack.sync import describe_candidate, run_sync
from jobtrack.timerange import as_of_bound, parse_date, with_time_range

console = Console()
err_console = Console(stderr=True)

DEFAULT_SINCE_DAYS = 365


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_auth(args: argparse.Namespace) -> int:
    ensure_data_dir()
    credentials_path = resolve_credentials(args.credentials)
    console.print(f"[dim]Using OAuth client:[/dim] {credentials_path}")

    creds = get_credentials(credentials_path=credentials_path, interactive=True)
    client = GmailClient(creds)
    try:
        email = client.profile_email()
    except Exception as exc:  # pragma: no cover - network dependent
        email = f"(could not verify: {exc})"

    console.print(
        f"[green]Authenticated[/green] — reading Gmail as [bold]{email}[/bold]"
    )
    console.print(f"[dim]Token cached at {TOKEN_PATH}[/dim]")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    if revoke():
        console.print("[green]Cached Gmail token removed.[/green]")
        return 0
    console.print("[dim]No cached token to remove.[/dim]")
    return 0


def _build_query(args: argparse.Namespace) -> str:
    base = args.query or default_query()
    return with_time_range(base, since=args.since, before=getattr(args, "before", None))


def _resolve_as_of(value: str | None) -> tuple[datetime | None, str | None]:
    """Turn an --as-of value into an exclusive upper bound."""
    if not value:
        return None, None
    try:
        return as_of_bound(value), None
    except ValueError as exc:
        return None, str(exc)


def _print_as_of(console: Console, value: str, count: int | None = None) -> None:
    suffix = f" — {count} application(s)" if count is not None else ""
    console.print(
        f"[dim]Replaying history as of {parse_date(value):%Y-%m-%d}{suffix}[/dim]"
    )


def _llm_config(args: argparse.Namespace) -> LLMConfig:
    config = LLMConfig.load()
    if getattr(args, "llm_provider", None):
        target = args.llm_provider
        if target in PROVIDER_DEFAULTS:
            config = LLMConfig.for_provider(target)
        else:
            config = LLMConfig(provider=target)
    if getattr(args, "llm_model", None):
        config.model = args.llm_model
    if getattr(args, "llm_url", None):
        config.base_url = args.llm_url.rstrip("/")
    if getattr(args, "llm_max_chars", None):
        config.max_chars = args.llm_max_chars
    return config


def _build_analyzer(args: argparse.Namespace) -> Analyzer:
    """Notes go to stderr so `sync --json` keeps stdout machine-readable."""
    return build_analyzer(
        _llm_config(args)
        if any(
            getattr(args, k, None)
            for k in ("llm_provider", "llm_model", "llm_url", "llm_max_chars")
        )
        else None,
        mode=getattr(args, "llm_mode", None),
        on_note=err_console.print,
    )


def cmd_sync(args: argparse.Namespace) -> int:
    ensure_data_dir()

    try:
        query = _build_query(args)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1

    if args.dry_run:
        console.print(f"[dim]Gmail query:[/dim] {query}")
        console.print("[yellow]Dry run — nothing will be written.[/yellow]")

    try:
        creds = get_credentials(
            credentials_path=resolve_credentials(args.credentials),
            token_path=TOKEN_PATH,
            interactive=not args.no_auth and sys.stdin.isatty(),
        )
    except AuthError as exc:
        err_console.print(f"[red]Not authenticated:[/red] {exc}")
        return 2

    analyzer = _build_analyzer(args)
    client = GmailClient(creds)

    def on_progress(scanned: int) -> None:
        status_text.update(f"[dim]Scanned {scanned} messages…[/dim]")

    def on_candidate(message, result, source) -> None:
        if args.dry_run:
            console.print(
                f"[dim]would record[/dim] {describe_candidate(message, result, source)}"
            )

    try:
        own_address = client.profile_email()
    except Exception:  # offline, or a scope change — not worth aborting over
        own_address = None

    # Read the saved setting unconditionally: relying on short-circuit ordering
    # hid a wrong attribute lookup whenever the flag was passed.
    saved = load_settings()
    track_sent = bool(getattr(args, "track_sent", False) or saved.track_sent)

    with Store(args.db) as store:
        with console.status("[dim]Scanning Gmail…[/dim]") as status_text:
            summary = run_sync(
                store,
                client,
                analyzer,
                query=query,
                limit=args.limit,
                recheck=args.recheck,
                dry_run=args.dry_run,
                own_address=own_address,
                track_sent=track_sent,
                since=getattr(args, "since", None),
                before=getattr(args, "before", None),
                on_progress=on_progress,
                on_candidate=on_candidate,
            )

    if summary.llm_disabled_reason:
        err_console.print(
            f"[yellow]LLM switched off for this run:[/yellow] {summary.llm_disabled_reason}"
        )

    if args.json:
        console.print_json(json.dumps(summary.to_dict()))
    else:
        print_sync_summary(console, summary.to_dict(), summary.applications)
    return 0


def cmd_llm_check(args: argparse.Namespace) -> int:
    config = _llm_config(args)
    console.print(f"[dim]Provider:[/dim] {config.provider}")
    console.print(f"[dim]Endpoint:[/dim] {config.base_url}")
    console.print(f"[dim]Model:[/dim]    {config.model}")

    try:
        llm = build_llm(config)
    except LLMError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 2

    usable, explanation = llm.check()
    if not usable:
        err_console.print(f"[red]{explanation}[/red]")
        return 2
    console.print(f"[green]{explanation}[/green]")

    console.print("[dim]Reading a sample rejection email…[/dim]")
    try:
        raw = llm.complete(SYSTEM_PROMPT, render_email(SAMPLE_EMAIL, config.max_chars))
        payload = extract_json(raw)
    except LLMError as exc:
        err_console.print(f"[red]The model failed:[/red] {exc}")
        return 2

    console.print_json(json.dumps(payload))

    result = interpret(payload)
    if result is None:
        console.print("[yellow]The model found no job event in this sample.[/yellow]")
        return 1
    console.print(
        f"[green]→ {result.kind}[/green]  company={result.company}  "
        f"role={result.role}  confidence={result.confidence}"
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from jobtrack.web import serve

    ensure_data_dir()

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
        err_console.print(
            f"[yellow]Refusing to bind {args.host}.[/yellow] The UI serves your email "
            "data with no authentication, so exposing it on the network must be "
            "deliberate. Pass [bold]--allow-remote[/bold] if you really mean it."
        )
        return 1

    display_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    console.print(
        f"[bold]jobtrack[/bold] → [link]http://{display_host}:{args.port}/[/link]"
    )
    if args.host in ("0.0.0.0", "::"):
        console.print(
            f"[yellow]Bound to {args.host}[/yellow] — reachable from your network."
        )
    console.print("[dim]Press Ctrl-C to stop.[/dim]")

    try:
        serve(
            db_path=args.db,
            host=args.host,
            port=args.port,
            credentials=args.credentials,
            llm_mode=args.llm_mode,
            open_browser=not args.no_browser,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
    return 0


STALE_AFTER = timedelta(hours=36)


def _token_health() -> tuple[bool, str]:
    """Try to obtain credentials without a browser. May refresh over the network."""
    try:
        get_credentials(
            credentials_path=resolve_credentials(None),
            token_path=TOKEN_PATH,
            interactive=False,
        )
    except AuthError as exc:
        return False, str(exc).splitlines()[0]
    except Exception as exc:  # network trouble, revoked grant, ...
        return False, f"{type(exc).__name__}: {exc}"
    return True, "valid"


def _last_sync(db_path: str) -> datetime | None:
    with Store(db_path) as store:
        raw = store.get_meta("last_sync_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _log_tail(path: Path, lines: int = 12) -> str | None:
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not content:
        return None
    return "\n".join(content.splitlines()[-lines:])


def cmd_schedule(args: argparse.Namespace) -> int:
    from jobtrack.schedule import (
        DEFAULT_HOUR,
        DEFAULT_MINUTE,
        DEFAULT_SINCE_DAYS,
        ScheduleError,
        ScheduleSpec,
        describe_next_run,
        get_scheduler,
        parse_at,
    )

    hour, minute = DEFAULT_HOUR, DEFAULT_MINUTE
    if getattr(args, "at", None):
        try:
            hour, minute = parse_at(args.at)
        except ScheduleError as exc:
            err_console.print(f"[red]{exc}[/red]")
            return 1

    spec = ScheduleSpec(
        hour=hour,
        minute=minute,
        since=args.since if args.since is not None else DEFAULT_SINCE_DAYS,
        db_path=str(Path(args.db).expanduser()),
        log_path=str(Path(DATA_DIR) / "sync.log"),
    )

    try:
        scheduler = get_scheduler(spec)
    except ScheduleError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1

    action = args.action or "status"

    if action == "install":
        try:
            message = scheduler.install()
        except ScheduleError as exc:
            err_console.print(f"[red]{exc}[/red]")
            return 1
        console.print(f"[green]{message}[/green]")
        console.print(f"[dim]Logs to {spec.log_path}[/dim]")
        console.print("[dim]Check it any time with `jobtrack schedule status`.[/dim]")
        return 0

    if action == "uninstall":
        try:
            message = scheduler.uninstall()
        except ScheduleError as exc:
            err_console.print(f"[red]{exc}[/red]")
            return 1
        console.print(message)
        return 0

    # -- status ------------------------------------------------------------
    details = scheduler.installed_details()
    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column("", style="dim", no_wrap=True)
    table.add_column("", overflow="fold")

    if details is None:
        table.add_row("Schedule", "[yellow]not installed[/yellow]")
    else:
        when = (
            f"daily at {details['hour']:02d}:{details['minute']:02d}"
            f" — next run {describe_next_run(details['hour'], details['minute'])}"
            if details.get("hour") is not None and details.get("minute") is not None
            else "installed (unrecognised schedule)"
        )
        table.add_row("Schedule", when)
        table.add_row("Mechanism", scheduler.describe())
        if details.get("since"):
            table.add_row("Lookback", f"last {details['since']} days of mail")
        if details.get("db"):
            table.add_row("Database", str(details["db"]))
        table.add_row("Command", f"[dim]{details['raw']}[/dim]")

    log_path = (
        Path(details["log"]) if details and details.get("log") else Path(spec.log_path)
    )
    table.add_row("Log", str(log_path))

    last = _last_sync(args.db)
    if last is None:
        table.add_row("Last sync", "[yellow]never[/yellow]")
    else:
        age = datetime.now(timezone.utc) - last
        stamp = last.astimezone().strftime("%Y-%m-%d %H:%M")
        table.add_row("Last sync", f"{_human_age(age)} ago ({stamp})")

    if args.skip_token_check:
        table.add_row("Token", "[dim]not checked[/dim]")
        token_ok = True
    else:
        token_ok, message = _token_health()
        table.add_row(
            "Token",
            "[green]ok[/green]" if token_ok else f"[red]failed[/red] — {message}",
        )

    console.print(table)

    if details is None:
        console.print(
            "\n[yellow]Not scheduled.[/yellow] Sync automatically with "
            "[bold]jobtrack schedule install --at 08:00[/bold]"
        )
        return 0

    if not token_ok:
        console.print(
            "\n[red]The saved Gmail token is not usable, so the daily sync is failing.[/red]\n"
            "Run [bold]jobtrack auth[/bold] to re-authorise — and see the README section\n"
            '"Making the token last" so you stop having to do this every week.'
        )
    elif last is not None and (datetime.now(timezone.utc) - last) > STALE_AFTER:
        console.print(
            f"\n[yellow]The last sync was {_human_age(datetime.now(timezone.utc) - last)} ago, "
            "which is longer than expected for a daily job.[/yellow]"
        )
        tail = _log_tail(log_path)
        if tail:
            console.print("[dim]Last lines of the log:[/dim]")
            console.print(f"[dim]{tail}[/dim]")

    return 0


def _human_age(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    days = seconds // 86400
    return f"{days}d"


def cmd_list(args: argparse.Namespace) -> int:
    as_of, error = _resolve_as_of(args.as_of)
    if error:
        err_console.print(f"[red]{error}[/red]")
        return 1

    statuses = args.status
    if args.active:
        statuses = list(ACTIVE_STATUSES)

    with Store(args.db) as store:
        applications = store.list_applications(
            statuses=statuses,
            company=args.company,
            as_of=as_of,
            category=getattr(args, "category", None),
        )

    if args.json:
        console.print_json(
            json.dumps([_application_dict(app) for app in applications], default=str)
        )
        return 0

    if as_of is not None:
        _print_as_of(console, args.as_of, len(applications))
    print_applications(console, applications, reference=as_of)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    as_of, error = _resolve_as_of(args.as_of)
    if error:
        err_console.print(f"[red]{error}[/red]")
        return 1

    with Store(args.db) as store:
        application = None
        if args.target.isdigit():
            application = store.get_application(int(args.target), as_of=as_of)
        if application is None:
            matches = store.find_application_by_name(args.target)
            if len(matches) == 1:
                application = matches[0]
            elif len(matches) > 1:
                err_console.print(
                    f"[yellow]{len(matches)} matches for[/yellow] '{args.target}':"
                )
                for match in matches:
                    err_console.print(
                        f"  [bold]{match.id}[/bold]  {match.company} — {match.role or '—'}"
                    )
                return 1

        if application is None:
            err_console.print(f"[red]No application matching[/red] '{args.target}'")
            return 1

        events = store.get_events(application.id, as_of=as_of)

    if args.json:
        payload = _application_dict(application)
        if as_of is not None:
            payload["as_of"] = parse_date(args.as_of).isoformat()
        payload["events"] = [
            {
                "kind": event.kind,
                "date": event.event_date.isoformat(),
                "subject": event.subject,
                "sender": event.sender,
                "snippet": event.snippet,
                "confidence": event.confidence,
                "matched": event.matched,
            }
            for event in events
        ]
        console.print_json(json.dumps(payload, default=str))
        return 0

    if as_of is not None:
        _print_as_of(console, args.as_of)
    print_application(console, application, events)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    as_of, error = _resolve_as_of(args.as_of)
    if error:
        err_console.print(f"[red]{error}[/red]")
        return 1

    with Store(args.db) as store:
        counts = store.status_counts(
            as_of=as_of, category=getattr(args, "category", None)
        )
        total_events = store.total_events(
            as_of=as_of, category=getattr(args, "category", None)
        )
        last_sync = store.get_meta("last_sync_at")

    if args.json:
        payload = {"statuses": counts, "events": total_events, "last_sync": last_sync}
        if as_of is not None:
            payload["as_of"] = parse_date(args.as_of).isoformat()
        console.print_json(json.dumps(payload))
        return 0

    if as_of is not None:
        _print_as_of(console, args.as_of)
    print_stats(console, counts, total_events)
    if last_sync and as_of is None:
        console.print(f"[dim]Last sync: {last_sync}[/dim]")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        applications = store.list_applications(category=getattr(args, "category", None))
        rows = []
        for app in applications:
            record = _application_dict(app)
            if args.with_events:
                record["events"] = [
                    {
                        "kind": event.kind,
                        "date": event.event_date.isoformat(),
                        "subject": event.subject,
                        "sender": event.sender,
                    }
                    for event in store.get_events(app.id)
                ]
            rows.append(record)

    if args.format == "json":
        output = json.dumps(rows, indent=2, default=str)
    else:
        buffer = io.StringIO()
        fieldnames = [
            "id",
            "company",
            "role",
            "status",
            "first_seen",
            "last_event_at",
            "event_count",
            "notes",
            "category",
            "follow_up_on",
            "status_override",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in rows:
            writer.writerow(record)
        output = buffer.getvalue()

    if args.output:
        Path(args.output).expanduser().write_text(output, encoding="utf-8")
        console.print(f"[green]Wrote {len(rows)} row(s) to[/green] {args.output}")
    else:
        console.print(output, end="", highlight=False, soft_wrap=True)
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    path = Path(args.db)
    if not path.is_file():
        console.print("[dim]Nothing to reset.[/dim]")
        return 0
    if not args.yes:
        err_console.print(
            f"[yellow]This deletes {path} and all tracked history.[/yellow] "
            "Re-run with [bold]--yes[/bold] to confirm."
        )
        return 1
    path.unlink()
    console.print(f"[green]Deleted[/green] {path}")
    return 0


def _application_dict(app) -> dict:
    return {
        "id": app.id,
        "company": app.company,
        "role": app.role,
        "status": app.status,
        "first_seen": app.first_seen.isoformat(),
        "last_event_at": app.last_event_at.isoformat(),
        "event_count": app.event_count,
        "notes": app.notes,
        "category": app.category,
        "follow_up_on": app.follow_up_on,
        "status_override": app.status_override,
    }


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------
def _add_llm_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--llm-mode",
        choices=MODES,
        help=(
            "off = rules only; auto = use the model if one is configured "
            "(default); fallback = model only when the rules are unsure; "
            "always = model decides every email"
        ),
    )
    parser.add_argument(
        "--llm-provider",
        choices=sorted(PROVIDER_DEFAULTS),
        help=(
            "ollama (local, default), gemini, or any OpenAI-compatible provider: "
            f"{', '.join(sorted(k for k in PROVIDER_DEFAULTS if k != 'ollama' and k != 'gemini'))}"
        ),
    )
    parser.add_argument("--llm-model", help="Override the provider's default model")
    parser.add_argument("--llm-url", help="Override the provider's base URL")
    parser.add_argument(
        "--llm-max-chars",
        type=int,
        help="How much of each email body to send (default: 4000)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobtrack",
        description="Track job applications and employer responses from your Gmail inbox.",
    )
    parser.add_argument(
        "--version", action="version", version=f"jobtrack {__version__}"
    )
    parser.add_argument(
        "--db",
        default=str(DB_PATH),
        help="Path to the SQLite database (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="Authorise read-only Gmail access")
    p_auth.add_argument(
        "--credentials", help="Path to the OAuth client secret JSON from Google Cloud"
    )
    p_auth.set_defaults(func=cmd_auth)

    p_logout = sub.add_parser("logout", help="Forget the cached Gmail token")
    p_logout.set_defaults(func=cmd_logout)

    p_sync = sub.add_parser("sync", help="Scan Gmail and update the tracker")
    p_sync.add_argument("--query", help="Override the Gmail search query")
    p_sync.add_argument(
        "--since",
        default=str(DEFAULT_SINCE_DAYS),
        help=(
            "How far back to scan: a number of days (30), a duration (3m, 2y), "
            "or a date (2023-01-01). 0 = no limit. (default: %(default)s)"
        ),
    )
    p_sync.add_argument(
        "--before",
        help="Only scan mail before this date (2024-06-01). Optional.",
    )
    p_sync.add_argument(
        "--limit", type=int, default=0, help="Max messages to fetch (0 = all)"
    )
    p_sync.add_argument(
        "--credentials", help="Path to the OAuth client secret JSON from Google Cloud"
    )
    p_sync.add_argument(
        "--no-auth",
        action="store_true",
        help="Never open a browser; fail if the stored token is unusable",
    )
    p_sync.add_argument(
        "--recheck",
        action="store_true",
        help="Re-classify messages that were already seen",
    )
    p_sync.add_argument(
        "--track-sent",
        action="store_true",
        help=(
            "Also record applications you sent yourself; without this your own "
            "mail is skipped (default: the saved setting)"
        ),
    )
    p_sync.add_argument(
        "--dry-run", action="store_true", help="Show what would be recorded"
    )
    p_sync.add_argument("--json", action="store_true", help="Emit a JSON summary")
    _add_llm_arguments(p_sync)
    p_sync.set_defaults(func=cmd_sync)

    p_llm = sub.add_parser(
        "llm-check", help="Verify the local model can read a sample email"
    )
    _add_llm_arguments(p_llm)
    p_llm.set_defaults(func=cmd_llm_check)

    p_serve = sub.add_parser("serve", help="Open the web UI in a browser")
    p_serve.add_argument(
        "--host", default=WEB_HOST, help="Bind address (default: %(default)s)"
    )
    p_serve.add_argument(
        "--port", type=int, default=WEB_PORT, help="Port (default: %(default)s)"
    )
    p_serve.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser"
    )
    p_serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="Permit binding to a non-localhost address (the UI has no authentication)",
    )
    p_serve.add_argument(
        "--credentials", help="Path to the OAuth client secret JSON from Google Cloud"
    )
    _add_llm_arguments(p_serve)
    p_serve.set_defaults(func=cmd_serve)

    p_sched = sub.add_parser("schedule", help="Run the sync automatically every day")
    p_sched.set_defaults(
        func=cmd_schedule, action="status", since=None, at=None, skip_token_check=False
    )
    sched_sub = p_sched.add_subparsers(dest="action")

    for name, help_text in (
        ("install", "Install a daily sync (launchd on macOS, cron on Linux)"),
        ("uninstall", "Remove the daily sync"),
        ("status", "Show whether the daily sync is configured and working"),
    ):
        sp = sched_sub.add_parser(name, help=help_text)
        sp.add_argument("--at", help="Time of day to run, HH:MM (default: 08:00)")
        sp.add_argument(
            "--since",
            type=int,
            help="How many days of mail each run should re-check (default: 7)",
        )
        if name == "status":
            sp.add_argument(
                "--skip-token-check",
                action="store_true",
                help="Do not contact Google to verify the saved token",
            )
        sp.set_defaults(func=cmd_schedule, action=name)

    p_list = sub.add_parser("list", help="Show tracked applications")
    p_list.add_argument(
        "--status", action="append", help="Filter by status (repeatable)"
    )
    p_list.add_argument("--company", help="Filter by company name")
    p_list.add_argument(
        "--active", action="store_true", help="Hide rejected applications"
    )
    p_list.add_argument(
        "--as-of",
        dest="as_of",
        help="Replay status as it stood on this date (2024-06-01)",
    )
    p_list.add_argument("--json", action="store_true", help="Emit JSON")
    p_list.add_argument(
        "--category",
        choices=["employment", "freelance"],
        help="Keep employment and freelance work separate",
    )
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show one application and its timeline")
    p_show.add_argument("target", help="Application id or a company/role search term")
    p_show.add_argument(
        "--as-of",
        dest="as_of",
        help="Hide events after this date and show the status back then",
    )
    p_show.add_argument("--json", action="store_true", help="Emit JSON")
    p_show.set_defaults(func=cmd_show)

    p_stats = sub.add_parser("stats", help="Summarise applications by status")
    p_stats.add_argument(
        "--as-of", dest="as_of", help="Count statuses as they stood on this date"
    )
    p_stats.add_argument("--json", action="store_true", help="Emit JSON")
    p_stats.add_argument(
        "--category",
        choices=["employment", "freelance"],
        help="Keep employment and freelance work separate",
    )
    p_stats.set_defaults(func=cmd_stats)

    p_export = sub.add_parser("export", help="Export applications to CSV or JSON")
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.add_argument("-o", "--output", help="Write to a file instead of stdout")
    p_export.add_argument(
        "--with-events",
        action="store_true",
        help="Include the email timeline (JSON only)",
    )
    p_export.add_argument(
        "--category",
        choices=["employment", "freelance"],
        help="Keep employment and freelance work separate",
    )
    p_export.set_defaults(func=cmd_export)

    p_reset = sub.add_parser("reset", help="Delete the local database")
    p_reset.add_argument("--yes", action="store_true", help="Confirm deletion")
    p_reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted.[/yellow]")
        return 130
    except AuthError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
