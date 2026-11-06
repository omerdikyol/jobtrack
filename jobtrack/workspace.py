"""Read models for the workspace. HTTP and presentation don't own domain rules."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from jobtrack import settings
from jobtrack.insights import upcoming
from jobtrack.models import ACTIVE_STATUSES, STATUS_LABELS, gmail_thread_url


def _when(stamp: str) -> str:
    moment = datetime.fromisoformat(stamp)
    text = f"{moment:%b} {moment.day}"
    return f"{text}, {moment:%H:%M}" if "T" in stamp else text


GOAL_LABELS = {
    "applications": "Applications",
    "replies": "Replies",
    "assessments": "Assessments",
    "interviews": "Interviews",
    "offers": "Offers",
}


def _period_start(moment: datetime, period: str):
    day = moment.date()
    return day.replace(day=1) if period == "month" else day - timedelta(days=day.weekday())


def _previous(start, period: str):
    if period == "week":
        return start - timedelta(weeks=1)
    return (start - timedelta(days=1)).replace(day=1)


def goal_progress(goals, moments: dict[str, dict[int, datetime]], now: datetime) -> list:
    """Each goal's count for the current week or month, and its streak.

    ``moments`` maps a metric to {application id: when it counted}, so an
    application counts once per metric: when it was first seen, first replied
    to, or first reached an assessment, interview, or offer.
    """
    progress = []
    for goal in goals:
        metric, period, target = goal["metric"], goal["period"], goal["target"]
        buckets: dict = {}
        for app_id, moment in moments[metric].items():
            buckets.setdefault(_period_start(moment, period), []).append(app_id)
        current = _period_start(now, period)
        streak, start = 0, _previous(current, period)
        while len(buckets.get(start, [])) >= target and streak < 520:
            streak += 1
            start = _previous(start, period)
        ids = buckets.get(current, [])
        progress.append(
            {
                **goal,
                "label": f"{GOAL_LABELS[metric]} per {period}",
                "current": len(ids),
                "streak": streak + (len(ids) >= target),
                "ids": ids,
                "period_start": current.isoformat(),
            }
        )
    return progress


def overview(store, as_of=None, category=None) -> dict:
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
    coming = upcoming(store, category=category, as_of=as_of)
    next_up = {}
    for item in coming:
        next_up.setdefault(item["application_id"], item)
    moments: dict[str, dict[int, datetime]] = {
        "applications": {a.id: a.first_seen for a in applications}
    }
    firsts = {"replies": response_kinds, "assessments": {"assessment"},
              "interviews": {"interview"}, "offers": {"offer"}}
    for metric in firsts:
        moments[metric] = {}
    for row in store.conn.execute(
        f"SELECT application_id, kind, MIN(event_date) AS first FROM events {where} "
        "GROUP BY application_id, kind",
        params,
    ):
        if row["application_id"] not in application_ids:
            continue
        when = datetime.fromisoformat(row["first"])
        for metric, kinds in firsts.items():
            if row["kind"] in kinds:
                known = moments[metric].get(row["application_id"])
                moments[metric][row["application_id"]] = min(known, when) if known else when
    attention = []
    for a in active:
        days = max(0, (now - a.last_event_at).days)
        reason = None
        soon = next_up.get(a.id)
        if soon:
            reason = (
                f"Interview {_when(soon['at'])}"
                if soon["kind"] == "interview"
                else f"Assessment due {_when(soon['at'])}"
            )
        elif a.follow_up_on and a.follow_up_on <= now.date().isoformat():
            reason = "Follow-up due"
        elif a.status in ("assessment", "interview"):
            reason = (
                "Interview in progress"
                if a.status == "interview"
                else "Assessment in progress"
            )
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
                    "at": soon["at"] if soon else None,
                }
            )
    attention.sort(
        key=lambda a: (
            a["at"] is None,
            a["at"] or "",
            a["reason"] != "Follow-up due",
            a["status"] not in ("interview", "assessment"),
            -a["days_since_update"],
        )
    )
    # Every week from the first application on (at least 8, at most 3 years),
    # so the chart can scroll back through the whole search.
    monday = (now - timedelta(days=now.weekday())).date()
    first = min((a.first_seen for a in applications), default=now)
    span = (monday - (first - timedelta(days=first.weekday())).date()).days // 7 + 1
    weeks = [
        {
            "week": (monday - timedelta(weeks=n - 1 - i)).isoformat(),
            "applications": 0,
            "responded": 0,
            "ids": [],
        }
        for n in [min(156, max(8, span))]
        for i in range(n)
    ]
    by_week = {w["week"]: w for w in weeks}
    for a in applications:
        week = by_week.get(
            (a.first_seen - timedelta(days=a.first_seen.weekday())).date().isoformat()
        )
        if week:
            week["applications"] += 1
            week["responded"] += bool(stages.get(a.id, set()) & response_kinds)
            week["ids"].append(a.id)
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
        "upcoming": coming[:10],
        "goals": goal_progress(settings.load().goals, moments, now),
        "last_sync": store.get_meta("last_sync_at"),
        "as_of": now.date().isoformat() if as_of else None,
    }
