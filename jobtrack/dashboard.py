"""Terminal rendering for the CLI dashboard."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from jobtrack.models import STATUS_LABELS, Application, Event

STATUS_STYLES = {
    "offer": "bold green",
    "rejected": "dim red",
    "interview": "bold cyan",
    "assessment": "bold yellow",
    "applied": "blue",
    "outreach": "magenta",
    "incomplete": "bold yellow",
}

KIND_STYLES = {
    "offer": "green",
    "rejected": "red",
    "interview": "cyan",
    "assessment": "yellow",
    "applied": "blue",
    "outreach": "magenta",
    "incomplete": "yellow",
}


def _relative(moment: datetime, reference: datetime | None = None) -> str:
    """Humanise how long ago something happened.

    ``reference`` lets the as-of view measure from the date being replayed
    rather than from today, which would otherwise read as nonsense.
    """
    now = reference or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = now - moment
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 3600:
        return f"{max(seconds // 60, 0)}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _status_text(status: str) -> Text:
    label = STATUS_LABELS.get(status, status.title())
    return Text(label, style=STATUS_STYLES.get(status, ""))


def _truncate(value: str | None, width: int) -> str:
    if not value:
        return "—"
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def applications_table(
    applications: list[Application], reference: datetime | None = None
) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Company", no_wrap=True)
    table.add_column("Role", overflow="ellipsis", max_width=34)
    table.add_column("Status")
    table.add_column("Last update", justify="right")
    table.add_column("Msgs", justify="right", style="dim")

    for app in applications:
        table.add_row(
            str(app.id),
            _truncate(app.company, 30),
            _truncate(app.role, 34),
            _status_text(app.status),
            f"{_relative(app.last_event_at, reference)}  "
            f"[dim]{app.last_event_at:%Y-%m-%d}[/dim]",
            str(app.event_count),
        )
    return table


def print_applications(
    console: Console, applications: list[Application], reference: datetime | None = None
) -> None:
    if not applications:
        console.print(
            "[dim]No applications yet. Run [bold]jobtrack sync[/bold] to scan your inbox.[/dim]"
        )
        return
    console.print(applications_table(applications, reference))
    console.print(
        f"[dim]{len(applications)} application(s). "
        "Use [bold]jobtrack show <id>[/bold] for the full timeline.[/dim]"
    )


def print_application(console: Console, app: Application, events: list[Event]) -> None:
    header = Text()
    header.append(app.company, style="bold")
    if app.role:
        header.append(f" — {app.role}", style="")
    header.append("  ")
    header.append(f"[{app.status_label}]", style=STATUS_STYLES.get(app.status, ""))

    console.print(Panel(header, subtitle=f"application #{app.id}", expand=False))

    timeline = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
    timeline.add_column("Date", style="dim", no_wrap=True)
    timeline.add_column("Event")
    timeline.add_column("Subject", overflow="ellipsis", max_width=52)
    timeline.add_column("From", overflow="ellipsis", max_width=26, style="dim")

    for event in events:
        timeline.add_row(
            f"{event.event_date:%Y-%m-%d}",
            Text(
                STATUS_LABELS.get(event.kind, event.kind),
                style=KIND_STYLES.get(event.kind, ""),
            ),
            _truncate(event.subject, 52),
            _truncate(event.sender, 26),
        )
    console.print(timeline)

    if events:
        latest = events[-1]
        if latest.snippet:
            console.print(
                Panel(latest.snippet.strip(), title="latest message", expand=False)
            )


def print_stats(console: Console, counts: dict[str, int], total_events: int) -> None:
    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("Status")
    table.add_column("Applications", justify="right")

    order = [
        "incomplete",
        "offer",
        "interview",
        "assessment",
        "applied",
        "outreach",
        "rejected",
    ]
    total = 0
    for status in order:
        count = counts.get(status, 0)
        if not count:
            continue
        total += count
        table.add_row(_status_text(status), str(count))
    if counts.get("unknown"):
        table.add_row(Text("Unknown", style="dim"), str(counts["unknown"]))
        total += counts["unknown"]

    console.print(table)
    console.print(
        f"[dim]{total} application(s), {total_events} email(s) tracked.[/dim]"
    )


def print_sync_summary(
    console: Console, summary: dict[str, int], conversations: int
) -> None:
    parts = [
        f"[green]{summary.get('created', 0)}[/green] new",
        f"[blue]{summary.get('matched', 0)}[/blue] updated",
        f"[dim]{summary.get('duplicate', 0)} already seen[/dim]",
        f"[dim]{summary.get('skipped', 0)} not job-related[/dim]",
    ]
    if summary.get("sent"):
        parts.append(
            f"[yellow]{summary['sent']}[/yellow] sent by you"
        )
    if summary.get("llm"):
        parts.append(f"[magenta]{summary['llm']}[/magenta] read by the model")
    console.print("Sync complete: " + ", ".join(parts))
    console.print(f"[dim]{conversations} application(s) tracked in total.[/dim]")
