"""Read models for the workspace. HTTP and presentation don't own domain rules."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from jobtrack.models import ACTIVE_STATUSES, STATUS_LABELS, gmail_thread_url
from jobtrack.roles import summarize_roles


def overview(store, as_of=None, category=None, categorizer=None) -> dict:
    applications = store.list_applications(as_of=as_of, category=category)
    now = (as_of - timedelta(microseconds=1)) if as_of else datetime.now(timezone.utc)
    counts = Counter(a.status for a in applications)
    active = [a for a in applications if a.status in ACTIVE_STATUSES]
    where, params = ("WHERE event_date < ?", [as_of.isoformat()]) if as_of else ("", [])
    application_ids = {a.id for a in applications}
    stages: dict[int, set] = {}
    for row in store.conn.execute(
        f"SELECT DISTINCT application_id, kind FROM events {where}", params
    ):
        if row["application_id"] in application_ids:
            stages.setdefault(row["application_id"], set()).add(row["kind"])
    response_kinds = {"assessment", "interview", "rejected", "offer"}
    submitted = [k for k in stages.values() if k & (response_kinds | {"applied"})]
    responses = sum(bool(k & response_kinds) for k in submitted)
    attention = []
    for a in active:
        days = max(0, (now - a.last_event_at).days)
        reason = None
        if a.follow_up_on and a.follow_up_on <= now.date().isoformat():
            reason = "Follow-up due"
        elif a.status in ("assessment", "interview"):
            reason = "In progress"
        elif days >= 14 and a.status == "applied":
            reason = f"No update in {days} days"
        elif a.status == "incomplete":
            reason = "Application incomplete"
        if reason:
            attention.append(
                {
                    "id": a.id,
                    "company": a.company,
                    "role": a.role,
                    "status": a.status,
                    "reason": reason,
                    "follow_up_on": a.follow_up_on,
                    "days_since_update": days,
                }
            )
    attention.sort(
        key=lambda a: (
            a["reason"] != "Follow-up due",
            a["status"] not in ("interview", "assessment"),
            -a["days_since_update"],
        )
    )
    monday = (now - timedelta(days=now.weekday())).date()
    weeks = [
        {"week": (monday - timedelta(weeks=7 - i)).isoformat(), "applications": 0}
        for i in range(8)
    ]
    for a in applications:
        start = (
            (a.first_seen - timedelta(days=a.first_seen.weekday())).date().isoformat()
        )
        for week in weeks:
            if week["week"] == start:
                week["applications"] += 1
    activity = []
    for row in store.recent_events(limit=12, as_of=as_of, category=category):
        activity.append(
            {
                "id": row["id"],
                "application_id": row["application_id"],
                "company": row["company"],
                "role": row["role"],
                "kind": row["kind"],
                "label": STATUS_LABELS.get(row["kind"], row["kind"]),
                "date": row["event_date"],
                "subject": row["subject"],
                "snippet": row["snippet"],
                "gmail_url": gmail_thread_url(row["thread_id"]),
            }
        )
    return {
        "total": len(applications),
        "active": len(active),
        "statuses": dict(counts),
        "interviewing": counts["interview"],
        "offers": counts["offer"],
        "responses": responses,
        "submitted": len(submitted),
        "response_rate": round(100 * responses / len(submitted)) if submitted else 0,
        "attention": attention,
        "activity": activity,
        "weeks": weeks,
        "role_categories": summarize_roles(applications, categorizer),
        "last_sync": store.get_meta("last_sync_at"),
        "as_of": now.date().isoformat() if as_of else None,
    }
