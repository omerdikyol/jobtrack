"""SQLite persistence for applications and their timeline of events."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from dataclasses import replace

from jobtrack.categories import CATEGORIES, identity_key, infer_category
from jobtrack.classify import fold, is_assessment_platform
from jobtrack.models import (
    STATUS_RANK,
    Application,
    Classification,
    EmailMessage,
    Event,
)
from jobtrack.schema import migrate

FALSE_POSITIVE_KEY = "manual:false-positive"

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company       TEXT NOT NULL,
    company_key   TEXT NOT NULL,
    role          TEXT,
    role_key      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_event_at TEXT NOT NULL,
    UNIQUE (company_key, role_key)
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    message_id     TEXT NOT NULL UNIQUE,
    thread_id      TEXT,
    kind           TEXT NOT NULL,
    subject        TEXT NOT NULL DEFAULT '',
    sender         TEXT NOT NULL DEFAULT '',
    event_date     TEXT NOT NULL,
    snippet        TEXT NOT NULL DEFAULT '',
    confidence     REAL NOT NULL DEFAULT 0.0,
    matched        TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_events_app ON events (application_id);
CREATE INDEX IF NOT EXISTS idx_events_thread ON events (thread_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|company|plc|sa|ag|bv|nv|oy|ab|pte)\b"
)


def normalize_company(name: str) -> str:
    """Collapse company names so 'Acme, Inc.' and 'acme' match."""
    text = name.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = _LEGAL_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_role(role: str | None) -> str:
    if not role:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", role.lower())).strip()


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _fold_name(text: str | None) -> str:
    """Recognize accent/case variants without rewriting historical identity keys."""
    return (
        unicodedata.normalize("NFKD", (text or "").casefold().replace("ı", "i"))
        .encode("ascii", "ignore")
        .decode()
    )


FOLLOW_UP_KINDS = ("rejected", "assessment", "interview", "offer")


def _mail_text(msg: EmailMessage) -> str:
    return fold(" ".join(filter(None, [msg.subject, msg.snippet, (msg.body or "")[:8000]])))


def _named_in(text: str, names: dict, case_sensitive: bool = False, min_length: int = 6):
    """The key of the one name that appears in ``text`` as whole words, else None.

    A name inside a longer matched name ("Software Engineer" in "Senior
    Software Engineer") yields to it; two unrelated names mean no answer.
    """
    haystack = text if case_sensitive else text.casefold()
    hits = {}
    for key, name in names.items():
        needle = fold(name or "").strip()
        if not case_sensitive:
            needle = needle.casefold()
        if len(needle) < min_length:
            continue
        if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack):
            hits[key] = needle
    longest = [
        key
        for key, needle in hits.items()
        if not any(other != needle and needle in other for other in hits.values())
    ]
    distinct = {hits[key] for key in longest}
    return longest[0] if len(distinct) == 1 else None


def _similar_titles(a: str, b: str) -> bool:
    """Reworded titles share most words; different jobs at one company do not.

    "software engineer i ccip" / "senior software engineer ccip" pass,
    "part time sw product design engineer" / "product design intern" do not.
    An unstated title is compatible with anything.
    """
    words_a, words_b = set((a or "").split()), set((b or "").split())
    if not words_a or not words_b:
        return True
    return len(words_a & words_b) / len(words_a | words_b) >= 0.5


def _different_requisition(a: str, b: str) -> bool:
    """Both titles carry a job number and the numbers differ ("3365" / "3246")."""
    ids_a = set(re.findall(r"\b\d{3,}\b", a or ""))
    ids_b = set(re.findall(r"\b\d{3,}\b", b or ""))
    return bool(ids_a and ids_b and not ids_a & ids_b)


def _word_prefix(a: str, b: str) -> bool:
    """True when one key is the other plus trailing words ("bcg" / "bcg x")."""
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return long == short or long.startswith(short + " ")


def _from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _application_from_row(row: sqlite3.Row, historical: bool = False) -> Application:
    return Application(
        category=row["category"],
        id=row["id"],
        company=row["company"],
        role=row["role"],
        status=row["asof_status"] if historical else row["status"],
        first_seen=_from_iso(row["asof_first"] if historical else row["first_seen"]),
        last_event_at=_from_iso(
            row["asof_last"] if historical else row["last_event_at"]
        ),
        event_count=row["event_count"],
        notes="" if historical else row["notes"],
        follow_up_on=None if historical else row["follow_up_on"],
        status_override=None if historical else row["status_override"],
    )


class Store:
    """Thin wrapper around a SQLite database holding the job history."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets the web UI keep reading while a background sync writes.
        self.conn.execute("PRAGMA journal_mode = WAL")
        # Lets the as-of queries rank event kinds in SQL, so replaying history
        # reuses exactly the same precedence rules as the live status.
        self.conn.create_function(
            "status_rank",
            1,
            lambda kind: STATUS_RANK.get(kind, -100),
            deterministic=True,
        )
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        migrate(self.conn)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes ------------------------------------------------------------
    def has_message(self, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def _find_application(self, msg: EmailMessage, cls: Classification) -> int | None:
        """Locate the application this email belongs to, if any.

        Threading wins, then an exact company+role match, then a company-wide
        match. Anything else starts a new application.
        """
        if msg.thread_id:
            rows = self.conn.execute(
                "SELECT DISTINCT a.id, a.role_key, a.role, a.company FROM events e JOIN applications a ON a.id=e.application_id WHERE e.thread_id = ?",
                (msg.thread_id,),
            ).fetchall()
            role_key = normalize_role(cls.role)
            compatible = [
                r
                for r in rows
                if (
                    not cls.company
                    or cls.company == "Unknown"
                    or r["company"] == "Unknown"
                    or normalize_company(_fold_name(r["company"]))
                    == normalize_company(_fold_name(cls.company))
                    or normalize_company(_fold_name(cls.company))
                    in self._company_aliases(r["id"])
                )
                and (
                    not role_key
                    or not r["role_key"]
                    or r["role_key"] == role_key
                    or normalize_role(_fold_name(r["role"]))
                    == normalize_role(_fold_name(cls.role))
                )
            ]
            if len(compatible) == 1:
                return int(compatible[0]["id"])

        if not cls.company or cls.company == "Unknown":
            return None
        company_key = identity_key(
            normalize_company(cls.company),
            infer_category(msg.sender, msg.subject, msg.snippet),
        )
        role_key = normalize_role(cls.role)
        rows = self.conn.execute(
            "SELECT id, role_key, role, last_event_at FROM applications WHERE company_key = ?",
            (company_key,),
        ).fetchall()
        if not rows:
            category = infer_category(msg.sender, msg.subject, msg.snippet)
            wanted = normalize_company(_fold_name(cls.company))
            candidates = self.conn.execute(
                "SELECT id, role_key, role, company, last_event_at FROM applications WHERE category = ?",
                (category,),
            ).fetchall()
            rows = [
                r
                for r in candidates
                if normalize_company(_fold_name(r["company"])) == wanted
            ]
            if not rows:
                # "Akamai" and "Akamai Technologies", "BCG" and "BCG X": the same
                # employer named with and without a qualifier. Roles still decide.
                rows = [
                    r
                    for r in candidates
                    if _word_prefix(normalize_company(_fold_name(r["company"])), wanted)
                ]
        exact = next(
            (
                r
                for r in rows
                if r["role_key"] == role_key
                or normalize_role(_fold_name(r["role"]))
                == normalize_role(_fold_name(cls.role))
            ),
            None,
        )
        if exact:
            return int(exact["id"])
        # "Software Engineer" and "Software Engineer - Data Foundation" are one
        # posting named twice; accept that only when a single application fits.
        if role_key and cls.kind != "outreach":
            similar = [
                r for r in rows if r["role_key"] and _word_prefix(r["role_key"], role_key)
            ]
            if len(similar) == 1:
                return int(similar[0]["id"])
        # Only attach incomplete identity to an unambiguous application. A
        # known, different role must never disappear into a company-wide match.
        if len(rows) == 1 and (not role_key or not rows[0]["role_key"]):
            return int(rows[0]["id"])
        # A rejection, test or interview answers an application; when the
        # company has exactly one still open, a reworded title ("Software
        # Engineer I, CCIP" / "Senior Software Engineer CCIP") is the same one.
        if (
            cls.kind in FOLLOW_UP_KINDS
            and len(rows) == 1
            and not _different_requisition(rows[0]["role_key"], role_key)
            and _similar_titles(rows[0]["role_key"], role_key)
        ):
            status = self.conn.execute(
                "SELECT status FROM applications WHERE id = ?", (rows[0]["id"],)
            ).fetchone()[0]
            if status not in ("rejected", "offer"):
                return int(rows[0]["id"])
        # Several applications at the company: the mail's own text often names
        # the role ("your test for the Forward Deployed AI Engineer role").
        # A mail that states its own, different title is a different job; only
        # an untitled one is placed by the title its text mentions.
        if len(rows) > 1 and not role_key:
            named = _named_in(_mail_text(msg), {r["id"]: r["role"] for r in rows})
            if named is not None:
                return int(named)
            # A reminder from the address that sent the test invitation
            # continues that application, even when it names no role.
            sender = (msg.sender_email or "").strip().lower()
            if sender and cls.kind in FOLLOW_UP_KINDS + ("incomplete",):
                ids = [r["id"] for r in rows]
                same_sender = {
                    row[0]
                    for row in self.conn.execute(
                        f"SELECT DISTINCT application_id FROM events WHERE application_id IN ({','.join('?' * len(ids))}) "
                        "AND instr(lower(sender), ?) > 0",
                        (*ids, sender),
                    )
                }
                if len(same_sender) == 1:
                    return int(same_sender.pop())
        return None

    def _company_from_text(self, msg: EmailMessage, cls: Classification) -> Classification:
        """Name the employer from the mail's text when the sender cannot.

        Assessment platforms (CodeSignal, Wonderlic, ...) write for the company
        the user applied to, and so does mail whose employer the rules missed.
        The text names it: only a company already tracked, and only one, counts.
        """
        known = cls.company and cls.company != "Unknown"
        if known and not (is_assessment_platform(msg) and not self._tracks_company(cls.company)):
            return cls
        companies = {
            row[0]: row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT company FROM applications WHERE company <> 'Unknown'"
            )
        }
        named = _named_in(_mail_text(msg), companies, case_sensitive=True, min_length=4)
        return replace(cls, company=named) if named else cls

    def _tracks_company(self, company: str) -> bool:
        wanted = normalize_company(_fold_name(company))
        return any(
            _word_prefix(normalize_company(_fold_name(row[0])), wanted)
            for row in self.conn.execute("SELECT DISTINCT company FROM applications")
        )

    def _company_aliases(self, application_id: int) -> list[str]:
        """Manual company renames retain the old name for same-thread followups."""
        return json.loads(self.get_meta(f"company_aliases:{application_id}", "[]"))

    def _create_application(self, msg: EmailMessage, cls: Classification) -> int:
        company = cls.company or "Unknown"
        category = infer_category(msg.sender, msg.subject, msg.snippet)
        cursor = self.conn.execute(
            """
            INSERT INTO applications
                (company, company_key, role, role_key, status, first_seen, last_event_at, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company,
                identity_key(
                    normalize_company(company)
                    if cls.company and company != "Unknown"
                    else f"unknown:{msg.message_id}",
                    category,
                ),
                cls.role,
                normalize_role(cls.role),
                cls.kind,
                _to_iso(msg.date),
                _to_iso(msg.date),
                category,
            ),
        )
        return int(cursor.lastrowid)

    def record(
        self, msg: EmailMessage, cls: Classification, replace: bool = False
    ) -> tuple[str, int | None]:
        """Persist one classified email.

        Returns ``(outcome, application_id)`` where outcome is one of
        ``created``, ``matched`` or ``duplicate``. With ``replace`` the existing
        event for this message is dropped first, so changed rules take effect.
        """
        if cls.kind not in STATUS_RANK:
            raise ValueError("Unknown event kind")
        if not msg.message_id:
            raise ValueError("An email must have a message ID")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            stale_app: int | None = None
            if self.has_message(msg.message_id):
                if not replace:
                    return "duplicate", None
                stale_app = self._event_application(msg.message_id)
                self.conn.execute(
                    "DELETE FROM events WHERE message_id = ?", (msg.message_id,)
                )

            cls = self._company_from_text(msg, cls)
            application_id = self._find_application(msg, cls)
            if application_id is None and stale_app is not None:
                original = self.get_application(stale_app)
                same_company = (
                    original
                    and cls.company
                    and normalize_company(_fold_name(original.company))
                    == normalize_company(_fold_name(cls.company))
                )
                same_role = original and (
                    not cls.role
                    or not original.role
                    or normalize_role(_fold_name(original.role))
                    == normalize_role(_fold_name(cls.role))
                )
                if same_company and same_role:
                    application_id = stale_app
            if application_id is None and stale_app is not None and not cls.company:
                application_id = stale_app
            if (
                application_id is None
                and stale_app is not None
                and cls.company == "Unknown"
            ):
                original = self.get_application(stale_app)
                if original and original.company == "Unknown":
                    application_id = stale_app
            outcome = "matched"
            if application_id is None:
                application_id = self._create_application(msg, cls)
                outcome = "created"

            self._insert_event(application_id, msg, cls)
            self._fill_application_role(application_id, cls)
            self._refresh_application(application_id)
            # A re-classification can move an email to a different application,
            # which changes the old one's derived status.
            if stale_app is not None and stale_app != application_id:
                self._refresh_application(stale_app)
            if replace:
                self._purge_empty_applications()
            return outcome, application_id

    def _event_application(self, message_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT application_id FROM events WHERE message_id = ?", (message_id,)
        ).fetchone()
        return int(row["application_id"]) if row else None

    def _insert_event(
        self, application_id: int, msg: EmailMessage, cls: Classification
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO events
                (application_id, message_id, thread_id, kind, subject, sender,
                 event_date, snippet, confidence, matched, review)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                application_id,
                msg.message_id,
                msg.thread_id,
                cls.kind,
                msg.subject,
                msg.sender,
                _to_iso(msg.date),
                msg.snippet,
                cls.confidence,
                json.dumps(cls.matched),
                json.dumps(cls.review) if cls.review else None,
            ),
        )

    def _fill_application_role(
        self, application_id: int, cls: Classification
    ) -> None:
        if not cls.role:
            return
        self.conn.execute(
            "UPDATE applications SET role = ?, role_key = ? WHERE id = ? AND role_key = '' "
            "AND NOT EXISTS (SELECT 1 FROM applications other WHERE other.company_key = applications.company_key AND other.role_key = ?)",
            (
                cls.role,
                normalize_role(cls.role),
                application_id,
                normalize_role(cls.role),
            ),
        )

    def _purge_empty_applications(self) -> None:
        self.conn.execute(
            "DELETE FROM applications WHERE id NOT IN "
            "(SELECT DISTINCT application_id FROM events)"
        )

    def _refresh_application(self, application_id: int) -> None:
        rows = self.conn.execute(
            "SELECT kind, event_date FROM events WHERE application_id = ?",
            (application_id,),
        ).fetchall()
        if not rows:
            return

        derived = max((r["kind"] for r in rows), key=lambda k: STATUS_RANK.get(k, 0))
        override = self.conn.execute(
            "SELECT status_override FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        status = override[0] or derived
        dates = sorted(r["event_date"] for r in rows)

        self.conn.execute(
            "UPDATE applications SET status = ?, first_seen = ?, last_event_at = ? WHERE id = ?",
            (status, dates[0], dates[-1], application_id),
        )

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    # -- reads -------------------------------------------------------------
    def list_applications(
        self,
        statuses: list[str] | None = None,
        company: str | None = None,
        search: str | None = None,
        as_of: datetime | None = None,
        category: str | None = None,
    ) -> list[Application]:
        """List applications, optionally as they stood at a past moment.

        With ``as_of`` the status is replayed from the events up to that
        instant instead of read from the stored column, and applications whose
        first event came later are left out entirely.
        """
        if as_of is not None:
            return [
                a
                for a in self._list_as_of(statuses, company, search, as_of)
                if category is None or a.category == category
            ]

        query = """
            SELECT a.*, COUNT(e.id) AS event_count
            FROM applications a
            LEFT JOIN events e ON e.application_id = a.id
        """
        clauses: list[str] = []
        params: list[object] = []
        if statuses:
            clauses.append(f"a.status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if company:
            clauses.append("a.company_key LIKE ?")
            params.append(f"%{normalize_company(company)}%")
        if search:
            clauses.append("(a.company_key LIKE ? OR LOWER(a.role) LIKE ?)")
            params.extend([f"%{normalize_company(search)}%", f"%{search.lower()}%"])
        if category:
            clauses.append("a.category = ?")
            params.append(category)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " GROUP BY a.id ORDER BY a.last_event_at DESC"

        rows = self.conn.execute(query, params).fetchall()
        return [_application_from_row(row) for row in rows]

    def _list_as_of(
        self,
        statuses: list[str] | None,
        company: str | None,
        search: str | None,
        as_of: datetime,
    ) -> list[Application]:
        bound = _to_iso(as_of)
        clauses = ["e.event_date < ?"]
        params: list[object] = [bound, bound]
        if company:
            clauses.append("a.company_key LIKE ?")
            params.append(f"%{normalize_company(company)}%")
        if search:
            clauses.append("(a.company_key LIKE ? OR LOWER(a.role) LIKE ?)")
            params.extend([f"%{normalize_company(search)}%", f"%{search.lower()}%"])

        # The inner JOIN drops applications with no activity yet at `as_of`, and
        # the correlated subquery replays the live precedence rules.
        query = f"""
            SELECT a.id, a.company, a.role, a.category,
                   COUNT(e.id) AS event_count,
                   MIN(e.event_date) AS asof_first,
                   MAX(e.event_date) AS asof_last,
                   (SELECT e2.kind FROM events e2
                     WHERE e2.application_id = a.id AND e2.event_date < ?
                     ORDER BY status_rank(e2.kind) DESC, e2.event_date DESC
                     LIMIT 1) AS asof_status
            FROM applications a
            JOIN events e ON e.application_id = a.id
            WHERE {" AND ".join(clauses)}
            GROUP BY a.id
            ORDER BY asof_last DESC
        """
        rows = self.conn.execute(query, params).fetchall()
        applications = [_application_from_row(row, historical=True) for row in rows]
        if statuses:
            wanted = set(statuses)
            applications = [a for a in applications if a.status in wanted]
        return applications

    def get_application(
        self, application_id: int, as_of: datetime | None = None
    ) -> Application | None:
        if as_of is not None:
            for application in self._list_as_of(None, None, None, as_of):
                if application.id == application_id:
                    return application
            return None

        row = self.conn.execute(
            """
            SELECT a.*, COUNT(e.id) AS event_count
            FROM applications a
            LEFT JOIN events e ON e.application_id = a.id
            WHERE a.id = ?
            GROUP BY a.id
            """,
            (application_id,),
        ).fetchone()
        if not row:
            return None
        return _application_from_row(row)

    def find_application_by_name(self, needle: str) -> list[Application]:
        rows = self.conn.execute(
            """
            SELECT a.*, COUNT(e.id) AS event_count
            FROM applications a
            LEFT JOIN events e ON e.application_id = a.id
            WHERE a.company_key LIKE ? OR LOWER(a.role) LIKE ?
            GROUP BY a.id
            ORDER BY a.last_event_at DESC
            """,
            (f"%{normalize_company(needle)}%", f"%{needle.lower()}%"),
        ).fetchall()
        return [_application_from_row(row) for row in rows]

    def get_events(
        self, application_id: int, as_of: datetime | None = None
    ) -> list[Event]:
        query = "SELECT * FROM events WHERE application_id = ? ORDER BY event_date ASC, id ASC"
        params: list[object] = [application_id]
        if as_of is not None:
            query = (
                "SELECT * FROM events WHERE application_id = ? AND event_date < ? "
                "ORDER BY event_date ASC, id ASC"
            )
            params.append(_to_iso(as_of))

        rows = self.conn.execute(query, params).fetchall()
        return [
            Event(
                id=r["id"],
                application_id=r["application_id"],
                message_id=r["message_id"],
                kind=r["kind"],
                subject=r["subject"],
                sender=r["sender"],
                event_date=_from_iso(r["event_date"]),
                snippet=r["snippet"],
                confidence=r["confidence"],
                matched=json.loads(r["matched"] or "[]"),
                thread_id=r["thread_id"],
                review=json.loads(r["review"]) if r["review"] else None,
            )
            for r in rows
        ]

    def related_context(self, msg: EmailMessage, cls: Classification | None) -> str:
        if cls is None:
            cls = Classification("applied", 0)
        app_id = self._find_application(msg, cls)
        if app_id is None:
            return ""
        app = self.get_application(app_id)
        events = [
            e
            for e in self.get_events(app_id)
            if e.message_id != msg.message_id and e.event_date <= msg.date
        ][-4:]
        return "\n".join(
            f"{e.event_date.isoformat()} | {e.sender} | {e.subject[:200]} | employer={app.company}, role={app.role or 'unstated'}, event={e.kind} | {e.snippet[:300]}"
            for e in events
        )

    def save_review(self, msg: EmailMessage, review: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO message_reviews(message_id,subject,sender,event_date,status,review) VALUES(?,?,?,?,?,?)",
                (
                    msg.message_id,
                    msg.subject,
                    msg.sender,
                    _to_iso(msg.date),
                    review["status"],
                    json.dumps(review),
                ),
            )

    def recent_reviews(self, status: str | None = None) -> list[dict]:
        where = "WHERE status = ?" if status else ""
        return [
            dict(r, review=json.loads(r["review"]))
            for r in self.conn.execute(
                f"SELECT * FROM message_reviews {where} ORDER BY event_date DESC LIMIT 30",
                (status,) if status else (),
            )
        ]

    def status_counts(
        self, as_of: datetime | None = None, category: str | None = None
    ) -> dict[str, int]:
        if as_of is not None or category:
            counts: dict[str, int] = {}
            for application in self.list_applications(as_of=as_of, category=category):
                counts[application.status] = counts.get(application.status, 0) + 1
            return counts

        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def total_events(
        self, as_of: datetime | None = None, category: str | None = None
    ) -> int:
        if category:
            where = "a.category = ?"
            params = [category]
            if as_of:
                where += " AND e.event_date < ?"
                params.append(_to_iso(as_of))
            return self.conn.execute(
                f"SELECT COUNT(*) FROM events e JOIN applications a ON a.id = e.application_id WHERE {where}",
                params,
            ).fetchone()[0]
        if as_of is not None:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE event_date < ?",
                (_to_iso(as_of),),
            ).fetchone()
            return int(row["n"])
        return int(
            self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        )

    def update_application(
        self, application_id: int, updates: dict
    ) -> Application | None:
        """User corrections belong to the application, independent of email sync."""
        current = self.get_application(application_id)
        if not current:
            return None
        allowed = {
            "company",
            "role",
            "notes",
            "follow_up_on",
            "status_override",
            "category",
        }
        if set(updates) - allowed:
            raise ValueError("Unknown application field")
        fields = dict(updates)
        category = fields.get("category", current.category)
        if category not in CATEGORIES:
            raise ValueError("Unknown application category")
        if "company" in fields:
            if not fields["company"] or not normalize_company(fields["company"]):
                raise ValueError("Company must contain a name")
        if "company" in fields or "category" in fields:
            old_key = self.conn.execute(
                "SELECT company_key FROM applications WHERE id = ?", (application_id,)
            ).fetchone()[0]
            base_key = (
                normalize_company(fields["company"])
                if "company" in fields
                else old_key.removeprefix("freelance:")
            )
            fields["company_key"] = identity_key(base_key, category)
        if "role" in fields:
            fields["role_key"] = normalize_role(fields["role"])
        if (
            fields.get("status_override")
            and fields["status_override"] not in STATUS_RANK
        ):
            raise ValueError("Unknown status")
        with self.conn:
            if fields:
                self.conn.execute(
                    f"UPDATE applications SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                    [*fields.values(), application_id],
                )
                if "company" in fields and fields["company"] != current.company:
                    aliases = self._company_aliases(application_id)
                    alias = normalize_company(_fold_name(current.company))
                    self.conn.execute(
                        "INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (
                            f"company_aliases:{application_id}",
                            json.dumps(sorted(set([*aliases, alias]))),
                        ),
                    )
            self._refresh_application(application_id)
        return self.get_application(application_id)

    def known_message_ids(self, analysis_key: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT message_id FROM events UNION SELECT message_id FROM ignored_messages WHERE analysis_key = ?",
            (analysis_key,),
        )
        return {row[0] for row in rows}

    def synced_message_ids(self) -> set[str]:
        """Normal sync never re-analyzes processed mail, even after model changes."""
        return {
            row[0]
            for row in self.conn.execute(
                "SELECT message_id FROM events UNION SELECT message_id FROM ignored_messages "
                "UNION SELECT message_id FROM message_reviews"
            )
        }

    def ignored_message_ids(self) -> set[str]:
        return {
            row[0]
            for row in self.conn.execute("SELECT message_id FROM ignored_messages")
        }

    def false_positive_message_ids(self) -> set[str]:
        """Messages suppressed by a manual "not an application" decision."""
        return {
            row[0]
            for row in self.conn.execute(
                "SELECT message_id FROM ignored_messages WHERE analysis_key = ?",
                (FALSE_POSITIVE_KEY,),
            )
        }

    def is_false_positive_message(self, message_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM ignored_messages WHERE message_id = ? AND analysis_key = ?",
                (message_id, FALSE_POSITIVE_KEY),
            ).fetchone()
            is not None
        )

    def list_false_positive_marks(self) -> dict:
        """Marks made from this build, plus older markers with no snapshot."""
        marks = []
        covered: set[str] = set()
        for row in self.conn.execute(
            "SELECT * FROM false_positive_marks ORDER BY created_at DESC, id DESC"
        ):
            message_ids = json.loads(row["message_ids"] or "[]")
            covered.update(message_ids)
            snapshot = json.loads(row["snapshot"] or "{}")
            marks.append(
                {
                    "id": row["id"],
                    "application_id": row["application_id"],
                    "company": row["company"],
                    "role": row["role"],
                    "category": row["category"],
                    "created_at": row["created_at"],
                    "message_count": len(message_ids),
                    "restorable": bool(snapshot.get("application")),
                }
            )
        legacy = [
            {"message_id": row[0]}
            for row in self.conn.execute(
                "SELECT message_id FROM ignored_messages WHERE analysis_key = ? "
                "ORDER BY rowid DESC",
                (FALSE_POSITIVE_KEY,),
            )
            if row[0] not in covered
        ]
        return {
            "marks": marks,
            "legacy": legacy,
            "count": len(marks) + len(legacy),
        }

    def mark_false_positive(self, application_id: int) -> int | None:
        """Remove an application, saving enough history to restore it later.

        The email ids stay in ``ignored_messages`` so later syncs — including a
        full recheck — never import them again.
        """
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            if row is None:
                return None
            events = self.conn.execute(
                "SELECT * FROM events WHERE application_id = ? "
                "ORDER BY event_date ASC, id ASC",
                (application_id,),
            ).fetchall()
            message_ids = [event["message_id"] for event in events]
            # Merged messages point at the application outside the event table.
            # Save them before the delete cascades, so a restore can reattach
            # them to the recreated application.
            merge_message_ids = [
                merged["message_id"]
                for merged in self.conn.execute(
                    "SELECT message_id FROM application_merge_messages "
                    "WHERE application_id = ? ORDER BY message_id",
                    (application_id,),
                )
            ]
            snapshot = {
                "application": {
                    "company": row["company"],
                    "company_key": row["company_key"],
                    "role": row["role"],
                    "role_key": row["role_key"],
                    "status": row["status"],
                    "first_seen": row["first_seen"],
                    "last_event_at": row["last_event_at"],
                    "category": row["category"],
                    "notes": row["notes"],
                    "follow_up_on": row["follow_up_on"],
                    "status_override": row["status_override"],
                    "location": dict(row).get("location"),
                },
                "events": [
                    {
                        "message_id": event["message_id"],
                        "thread_id": event["thread_id"],
                        "kind": event["kind"],
                        "subject": event["subject"],
                        "sender": event["sender"],
                        "event_date": event["event_date"],
                        "snippet": event["snippet"],
                        "confidence": event["confidence"],
                        "matched": json.loads(event["matched"] or "[]"),
                        "review": json.loads(event["review"])
                        if event["review"]
                        else None,
                    }
                    for event in events
                ],
                "merge_message_ids": merge_message_ids,
            }
            self.conn.execute(
                "INSERT INTO false_positive_marks"
                "(application_id, company, role, category, message_ids, snapshot, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    application_id,
                    row["company"],
                    row["role"],
                    row["category"],
                    json.dumps(message_ids),
                    json.dumps(snapshot),
                    _to_iso(datetime.now(timezone.utc)),
                ),
            )
            for message_id in message_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO ignored_messages(message_id, analysis_key) "
                    "VALUES (?, ?)",
                    (message_id, FALSE_POSITIVE_KEY),
                )
                self.conn.execute(
                    "DELETE FROM message_reviews WHERE message_id = ?", (message_id,)
                )
            self.conn.execute(
                "DELETE FROM applications WHERE id = ?", (application_id,)
            )
        return len(message_ids)

    def restore_false_positive(self, mark_id: int) -> int | None:
        """Recreate a marked application and its timeline, transactionally."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT * FROM false_positive_marks WHERE id = ?", (mark_id,)
            ).fetchone()
            if row is None:
                return None
            snapshot = json.loads(row["snapshot"] or "{}")
            application = snapshot.get("application")
            if not application:
                raise ValueError("This mark has no saved application snapshot")
            events = snapshot.get("events") or []
            message_ids = json.loads(row["message_ids"] or "[]")
            application_columns = {
                column[1]
                for column in self.conn.execute("PRAGMA table_info(applications)")
            }
            has_location = "location" in application_columns

            company_key = application.get("company_key") or identity_key(
                normalize_company(application.get("company") or "Unknown"),
                application.get("category") or "employment",
            )
            role_key = application.get("role_key")
            if role_key is None:
                role_key = normalize_role(application.get("role"))
            existing = self.conn.execute(
                "SELECT id FROM applications WHERE company_key = ? AND role_key = ?",
                (company_key, role_key),
            ).fetchone()
            if existing is not None:
                application_id = int(existing["id"])
                # An application that already exists may carry user edits made
                # after the mark. Only fill blanks; never overwrite them.
                fields = [
                    "notes = CASE WHEN notes = '' THEN ? ELSE notes END",
                    "follow_up_on = COALESCE(follow_up_on, ?)",
                    "status_override = COALESCE(status_override, ?)",
                ]
                params = [
                    application.get("notes") or "",
                    application.get("follow_up_on"),
                    application.get("status_override"),
                ]
                if has_location:
                    fields.append(
                        "location = COALESCE(NULLIF(location, ''), ?)"
                    )
                    params.append(application.get("location"))
                params.append(application_id)
                self.conn.execute(
                    f"UPDATE applications SET {', '.join(fields)} WHERE id = ?",
                    params,
                )
            else:
                columns = [
                    "company",
                    "company_key",
                    "role",
                    "role_key",
                    "status",
                    "first_seen",
                    "last_event_at",
                    "category",
                    "notes",
                    "follow_up_on",
                    "status_override",
                ]
                values = [
                    application.get("company") or "Unknown",
                    company_key,
                    application.get("role"),
                    role_key,
                    application.get("status") or "applied",
                    application.get("first_seen")
                    or _to_iso(datetime.now(timezone.utc)),
                    application.get("last_event_at")
                    or _to_iso(datetime.now(timezone.utc)),
                    application.get("category") or "employment",
                    application.get("notes") or "",
                    application.get("follow_up_on"),
                    application.get("status_override"),
                ]
                if has_location:
                    columns.append("location")
                    values.append(application.get("location"))
                cursor = self.conn.execute(
                    f"INSERT INTO applications ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in values)})",
                    values,
                )
                application_id = int(cursor.lastrowid)

            for event in events:
                # The event's message id is unique. If it is already in the
                # workspace (perhaps under another application), keep that row.
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO events
                        (application_id, message_id, thread_id, kind, subject,
                         sender, event_date, snippet, confidence, matched, review)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        application_id,
                        event.get("message_id"),
                        event.get("thread_id"),
                        event.get("kind") or "applied",
                        event.get("subject") or "",
                        event.get("sender") or "",
                        event.get("event_date")
                        or _to_iso(datetime.now(timezone.utc)),
                        event.get("snippet") or "",
                        event.get("confidence") or 0.0,
                        json.dumps(event.get("matched") or []),
                        json.dumps(event.get("review"))
                        if event.get("review")
                        else None,
                    ),
                )
            # Reattach merge mappings captured before the mark. A message that
            # was merged elsewhere while this application was gone keeps its
            # newer mapping; the primary key makes this conflict-safe.
            for merged_message_id in snapshot.get("merge_message_ids") or []:
                self.conn.execute(
                    "INSERT OR IGNORE INTO application_merge_messages"
                    "(message_id, application_id) VALUES (?, ?)",
                    (merged_message_id, application_id),
                )
            if message_ids:
                marks = ",".join("?" for _ in message_ids)
                self.conn.execute(
                    f"DELETE FROM ignored_messages WHERE message_id IN ({marks}) "
                    "AND analysis_key = ?",
                    [*message_ids, FALSE_POSITIVE_KEY],
                )
            self.conn.execute(
                "DELETE FROM false_positive_marks WHERE id = ?", (mark_id,)
            )
            self._refresh_application(application_id)
        return application_id

    def restore_legacy_false_positive(
        self, msg: EmailMessage, cls: Classification
    ) -> int | None:
        """Recover one pre-snapshot mark from its Gmail message.

        The old image did not save application or event details, so this only
        records what the message itself proves. The marker is cleared only
        after the event is stored, in the same transaction.
        """
        if cls.kind not in STATUS_RANK:
            raise ValueError("Unknown event kind")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if not self.is_false_positive_message(msg.message_id):
                return None
            application_id = self._event_application(msg.message_id)
            if application_id is None:
                application_id = self._find_application(msg, cls)
                if application_id is None:
                    application_id = self._create_application(msg, cls)
                self._insert_event(application_id, msg, cls)
                self._fill_application_role(application_id, cls)
                self._refresh_application(application_id)
            self.conn.execute(
                "DELETE FROM ignored_messages WHERE message_id = ? AND analysis_key = ?",
                (msg.message_id, FALSE_POSITIVE_KEY),
            )
        return application_id

    def ignore_message(self, message_id: str, analysis_key: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO ignored_messages VALUES (?, ?)",
                (message_id, analysis_key),
            )

    def remove_message(self, message_id: str) -> None:
        """Rechecking can remove a previously misclassified event."""
        with self.conn:
            app_id = self._event_application(message_id)
            if app_id is None:
                return
            self.conn.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
            self._refresh_application(app_id)
            self._purge_empty_applications()

    def recent_events(
        self,
        limit: int = 12,
        as_of: datetime | None = None,
        category: str | None = None,
    ) -> list[dict]:
        where = "WHERE e.event_date < ?" if as_of else ""
        params = [_to_iso(as_of)] if as_of else []
        if category:
            where += (" AND " if where else "WHERE ") + "a.category = ?"
            params.append(category)
        params.append(limit)
        return [
            dict(r)
            for r in self.conn.execute(
                f"""SELECT e.*, a.company, a.role, a.category FROM events e
                JOIN applications a ON a.id = e.application_id {where}
                ORDER BY e.event_date DESC, e.id DESC LIMIT ?""",
                params,
            )
        ]

    def _recover_sync(self) -> None:
        for row in self.conn.execute(
            "SELECT id, owner_pid FROM sync_runs WHERE status = 'running'"
        ).fetchall():
            try:
                os.kill(row["owner_pid"], 0)
            except ProcessLookupError:
                self.conn.execute(
                    "UPDATE sync_runs SET status = 'error', error = ?, finished_at = ? WHERE id = ?",
                    (
                        "Sync interrupted when its process stopped. Run it again to continue.",
                        _to_iso(datetime.now(timezone.utc)),
                        row["id"],
                    ),
                )
            except PermissionError:
                pass  # A live process owned by another user still holds its job.

    def begin_sync(self, query: str, dry_run: bool = False) -> int:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._recover_sync()
            if self.conn.execute(
                "SELECT 1 FROM sync_runs WHERE status = 'running'"
            ).fetchone():
                raise SyncBusyError("A sync is already running")
            cursor = self.conn.execute(
                "INSERT INTO sync_runs(status, started_at, owner_pid, query, dry_run) VALUES ('running', ?, ?, ?, ?)",
                (_to_iso(datetime.now(timezone.utc)), os.getpid(), query, int(dry_run)),
            )
        return int(cursor.lastrowid)

    def sync_progress(self, run_id: int, scanned: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sync_runs SET scanned = ? WHERE id = ? AND status = 'running'",
                (scanned, run_id),
            )

    def finish_sync(
        self, run_id: int, summary: dict | None = None, error: str | None = None
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sync_runs SET status = ?, finished_at = ?, summary = COALESCE(?, summary), error = ?, scanned = COALESCE(?, scanned) WHERE id = ?",
                (
                    "error" if error else "done",
                    _to_iso(datetime.now(timezone.utc)),
                    json.dumps(summary) if summary else None,
                    error,
                    summary.get("scanned", 0) if summary else None,
                    run_id,
                ),
            )

    def sync_history(self, limit: int = 20) -> list[dict]:
        with self.conn:
            self._recover_sync()
        result = []
        for row in self.conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
        ):
            item = dict(row)
            item.pop("owner_pid")
            item["summary"] = json.loads(item["summary"]) if item["summary"] else None
            item["dry_run"] = bool(item["dry_run"])
            result.append(item)
        return result


class SyncBusyError(RuntimeError):
    """The web app and scheduler share a database-level sync reservation."""
