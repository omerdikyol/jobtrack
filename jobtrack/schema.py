"""Additive, versioned SQLite migrations. Existing email history stays intact."""

from __future__ import annotations

import sqlite3

from jobtrack.categories import identity_key, infer_category

VERSION = 3


def migrate(conn: sqlite3.Connection) -> None:
    # Serialize startup across the web server and scheduled CLI processes.
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > VERSION:
            raise RuntimeError("This database needs a newer version of jobtrack.")
        if version < 1:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(applications)")
            }
            for name, definition in {
                "notes": "TEXT NOT NULL DEFAULT ''",
                "follow_up_on": "TEXT",
                "status_override": "TEXT",
            }.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE applications ADD COLUMN {name} {definition}"
                    )
            conn.execute("""CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                owner_pid INTEGER NOT NULL,
                query TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 0,
                scanned INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                error TEXT
            )""")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_single_sync ON sync_runs(status) WHERE status = 'running'"
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS ignored_messages (
                message_id TEXT NOT NULL,
                analysis_key TEXT NOT NULL,
                PRIMARY KEY (message_id, analysis_key)
            )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_applications_activity ON applications(last_event_at DESC)"
            )
            conn.execute("PRAGMA user_version = 1")

        if version < 2:
            conn.execute(
                "ALTER TABLE applications ADD COLUMN category TEXT NOT NULL DEFAULT 'employment' CHECK(category IN ('employment', 'freelance'))"
            )
            for app in conn.execute(
                "SELECT id, company_key FROM applications"
            ).fetchall():
                emails = conn.execute(
                    "SELECT sender, subject, snippet FROM events WHERE application_id = ?",
                    (app["id"],),
                ).fetchall()
                if any(infer_category(*email) == "freelance" for email in emails):
                    conn.execute(
                        "UPDATE applications SET category = 'freelance', company_key = ? WHERE id = ?",
                        (identity_key(app["company_key"], "freelance"), app["id"]),
                    )
            conn.execute(
                "CREATE INDEX idx_applications_category ON applications(category, last_event_at DESC)"
            )
            conn.execute("PRAGMA user_version = 2")

        if version < 3:
            conn.execute("ALTER TABLE events ADD COLUMN review TEXT")
            conn.execute("""CREATE TABLE message_reviews (
                message_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                sender TEXT NOT NULL,
                event_date TEXT NOT NULL,
                status TEXT NOT NULL,
                review TEXT NOT NULL
            )""")
            conn.execute(
                "CREATE INDEX idx_reviews_status ON message_reviews(status, event_date DESC)"
            )
            conn.execute("PRAGMA user_version = 3")
