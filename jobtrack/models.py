"""Plain data structures shared across the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_INVISIBLE_RE = re.compile(r"[\u034f\u200b\u2060\ufeff]")


def _split_addresses(header: str) -> list[tuple[str, str]]:
    """Split an address header into ``(display name, address)`` pairs.

    Display names may contain commas, so quoted spans are skipped rather than
    split on.
    """
    if not header:
        return []
    parts: list[tuple[str, str]] = []
    current: list[str] = []
    quoted = False
    for char in header:
        if char == '"':
            quoted = not quoted
        if char == "," and not quoted:
            parts.append(("".join(current).strip()))
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())

    result: list[tuple[str, str]] = []
    for part in parts:
        if "<" in part and part.endswith(">"):
            name = part[: part.rindex("<")].strip().strip('"')
            address = part[part.rindex("<") + 1 : -1].strip()
        else:
            name, address = "", part.strip()
        if address:
            result.append((name, address))
    return result


# Which event wins when several apply to the same application. `offer` beats
# everything, then a rejection, then the most advanced positive stage reached.
# `incomplete` sits below even outreach: it means "started but never submitted",
# so any real activity supersedes it.
STATUS_RANK = {
    "incomplete": -1,
    "outreach": 0,
    "applied": 1,
    "assessment": 2,
    "interview": 3,
    "rejected": 4,
    "offer": 5,
}

# Labels people actually want to read in the dashboard.
STATUS_LABELS = {
    "incomplete": "Incomplete",
    "outreach": "Outreach",
    "applied": "Applied",
    "assessment": "Assessment",
    "interview": "Interviewing",
    "rejected": "Rejected",
    "offer": "Offer",
}

# Statuses that still deserve attention, i.e. not a finished outcome.
ACTIVE_STATUSES = ("incomplete", "outreach", "applied", "assessment", "interview")


@dataclass
class EmailMessage:
    """A single Gmail message reduced to the fields we care about."""

    message_id: str
    thread_id: str
    subject: str
    sender: str
    date: datetime
    snippet: str = ""
    body: str = ""
    # Outbound mail carries the employer in the recipient, not the sender, so an
    # application sent by the user keeps the To: header. `to` is a raw header
    # list ("Name <a@b.co>, other@d.com"); `to_emails` splits it.
    to: str = ""

    def __post_init__(self) -> None:
        if self.date.tzinfo is None:
            self.date = self.date.replace(tzinfo=timezone.utc)
        # Preview padding must not hide the job card from a bounded model prompt.
        for name in ("subject", "snippet", "body"):
            setattr(
                self,
                name,
                re.sub(r"[\u034f\u200b\u2060\ufeff]", "", getattr(self, name)),
            )
        self.to = _INVISIBLE_RE.sub("", self.to or "")

    @property
    def sender_email(self) -> str:
        if "<" in self.sender and ">" in self.sender:
            return self.sender[
                self.sender.rindex("<") + 1 : self.sender.rindex(">")
            ].strip()
        return self.sender.strip()

    @property
    def sender_name(self) -> str:
        if "<" in self.sender:
            return self.sender[: self.sender.rindex("<")].strip().strip('"')
        return ""

    @property
    def to_emails(self) -> list[str]:
        """Addresses from the To: header, in order, without display names."""
        return [address for _, address in _split_addresses(self.to)]

    @property
    def to_email(self) -> str:
        """The first To: address — the employer for an outbound application."""
        emails = self.to_emails
        return emails[0] if emails else ""

    @property
    def to_name(self) -> str:
        """Display name of the first To: address, if Gmail supplied one."""
        for name, _ in _split_addresses(self.to):
            return name
        return ""


@dataclass
class Classification:
    """Result of running the rules over one email."""

    kind: str
    score: float
    company: str | None = None
    role: str | None = None
    matched: list[str] = field(default_factory=list)
    confidence_override: float | None = None
    review: dict | None = None

    @property
    def confidence(self) -> float:
        """Squash the raw rule score into a friendly 0-1 confidence."""
        if self.confidence_override is not None:
            return max(0.0, min(1.0, self.confidence_override))
        if self.score <= 0:
            return 0.0
        return min(1.0, round(self.score / 12.0, 2))


@dataclass
class Application:
    id: int
    company: str
    role: str | None
    status: str
    first_seen: datetime
    last_event_at: datetime
    event_count: int = 0
    notes: str = ""
    follow_up_on: str | None = None
    status_override: str | None = None
    category: str = "employment"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.title())


@dataclass
class Event:
    id: int
    application_id: int
    message_id: str
    kind: str
    subject: str
    sender: str
    event_date: datetime
    snippet: str = ""
    confidence: float = 0.0
    matched: list[str] = field(default_factory=list)
    thread_id: str | None = None
    review: dict | None = None

    @property
    def source(self) -> str:
        """Which engine produced this event — the local model or the rules."""
        return "llm" if "llm" in self.matched else "rules"

    @property
    def gmail_url(self) -> str | None:
        return gmail_thread_url(self.thread_id)


# Gmail message ids and thread ids are hex-ish; anything else is not one.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def gmail_thread_url(thread_id: str | None, mailbox: int = 0) -> str | None:
    """Deep link that opens the conversation in Gmail on the web.

    The `#all/` route works regardless of which label the thread sits under,
    which `#inbox/` does not.
    """
    if not thread_id or not _SAFE_ID.match(thread_id):
        return None
    return f"https://mail.google.com/mail/u/{mailbox}/#all/{thread_id}"
