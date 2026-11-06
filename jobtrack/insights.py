"""Read models that look across applications: what works, who replies, what's next.

Everything here is computed from the existing applications and events. The
caveat that matters: "first seen" is the first email jobtrack saw, so an
application known only from its rejection has no measurable reply time and
is left out of the timing figures.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from jobtrack.classify import ATS_DOMAINS, _domain_of, _root_domain
from jobtrack.models import STATUS_LABELS, gmail_thread_url
from jobtrack.roles import role_category
from jobtrack.store import _from_iso, _to_iso, normalize_company

RESPONSE_KINDS = {"assessment", "interview", "rejected", "offer"}
SUBMITTED_KINDS = RESPONSE_KINDS | {"applied"}
QUICK_REJECTION_DAYS = 3
COOLDOWN_DAYS = 182  # many employers ask you to wait six months to reapply

_CHANNEL_NAMES = {
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "kariyer.net": "Kariyer.net",
    "wellfound.com": "Wellfound",
    "angel.co": "Wellfound",
    "workatastartup.com": "Work at a Startup",
}
_REJECTION_REASONS = [
    (
        "position_filled",
        "Position filled or closed",
        re.compile(
            r"position (?:has been|was|is) (?:filled|closed)|role (?:has been|was|is) (?:filled|closed)|"
            r"no longer (?:hiring|available|open)|on hold|pozisyon (?:doldu|kapan)|alım(?:ı)? durdur",
            re.I,
        ),
    ),
    (
        "other_candidates",
        "Went with other candidates",
        re.compile(
            r"other candidates|another candidate|candidates whose|more closely (?:match|align)|"
            r"better (?:match|fit)|diğer aday|başka bir aday|daha uygun aday",
            re.I,
        ),
    ),
]
_AUTOMATED_SENDER = re.compile(
    r"^(?:no-?reply|noreply|do-?not-?reply|notifications?|mailer|bounce|jobs-noreply|"
    r"invitations|messages-noreply|alerts?)",
    re.I,
)


def channel(sender: str, matched: list[str]) -> str:
    """Where the first email of an application came from."""
    if "manual" in matched:
        return "Added manually"
    if "outbound application" in matched:
        return "Sent by you"
    address = sender[sender.rfind("<") + 1 :].rstrip(">").strip() if "<" in sender else sender
    root = _root_domain(_domain_of(address))
    if root in _CHANNEL_NAMES:
        return _CHANNEL_NAMES[root]
    if root in ATS_DOMAINS:
        return "Applicant tracking system"
    return "Company email"


def rejection_reason(events: list[dict]) -> tuple[str, str]:
    """(key, label) for why an application was rejected, as far as mail tells."""
    kinds = [e["kind"] for e in events]
    first_rejection = kinds.index("rejected")
    before = set(kinds[:first_rejection])
    if before & {"interview", "offer"}:
        return "after_interview", "After an interview"
    if "assessment" in before:
        return "after_assessment", "After an assessment"
    text = " ".join(
        f"{e['subject']} {e['snippet']}" for e in events if e["kind"] == "rejected"
    )
    for key, label, pattern in _REJECTION_REASONS:
        if pattern.search(text):
            return key, label
    return "not_stated", "No reason given"


def _events_by_application(store, as_of=None) -> dict[int, list[dict]]:
    where, params = ("WHERE event_date < ?", [_to_iso(as_of)]) if as_of else ("", [])
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in store.conn.execute(
        f"SELECT id, application_id, kind, event_date, sender, subject, snippet, "
        f"matched, thread_id, details FROM events {where} ORDER BY event_date, id",
        params,
    ):
        event = dict(row)
        event["date"] = _from_iso(row["event_date"])
        event["matched"] = json.loads(row["matched"] or "[]")
        event["details"] = json.loads(row["details"]) if row["details"] else None
        grouped[row["application_id"]].append(event)
    return grouped


def _profile(application, events: list[dict]) -> dict:
    kinds = {e["kind"] for e in events}
    first = events[0] if events else None
    first_response = next((e for e in events if e["kind"] in RESPONSE_KINDS), None)
    days = None
    # Only measurable when something (a confirmation, your own mail) came first.
    if first_response is not None and first is not None and first is not first_response:
        days = max(0, (first_response["date"] - first["date"]).days)
    first_test = next((e for e in events if e["kind"] == "assessment"), None)
    profile = {
        "id": application.id,
        # An interview or offer after the first assessment means it was passed.
        "passed_assessment": first_test is not None
        and any(
            e["kind"] in ("interview", "offer") and e["date"] >= first_test["date"]
            for e in events
        ),
        "company": application.company,
        "company_key": normalize_company(application.company) or application.company,
        "role": application.role,
        "status": application.status,
        "kinds": kinds,
        "submitted": bool(kinds & SUBMITTED_KINDS),
        "responded": bool(kinds & RESPONSE_KINDS),
        "days_to_response": days,
        "channel": channel(first["sender"], first["matched"]) if first else "Company email",
        "first_seen": application.first_seen,
        "last_event_at": application.last_event_at,
        "rejection": None,
        "quick_rejection": False,
    }
    if "rejected" in kinds:
        profile["rejection"] = rejection_reason(events)
        rejected_at = next(e["date"] for e in events if e["kind"] == "rejected")
        profile["quick_rejection"] = (
            profile["rejection"][0] in ("not_stated", "position_filled", "other_candidates")
            and (rejected_at - application.first_seen).days <= QUICK_REJECTION_DAYS
        )
    return profile


def _rate(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def insights(store, as_of=None, category=None, categorizer=None) -> dict:
    applications = store.list_applications(as_of=as_of, category=category)
    now = (as_of - timedelta(microseconds=1)) if as_of else datetime.now(timezone.utc)
    events = _events_by_application(store, as_of)
    profiles = [_profile(a, events.get(a.id, [])) for a in applications]
    submitted = [p for p in profiles if p["submitted"]]
    responded = [p for p in submitted if p["responded"]]

    def reached(*kinds):
        return [p["id"] for p in submitted if p["kinds"] & set(kinds)]

    funnel = [
        {"key": "submitted", "label": "Submitted", "ids": [p["id"] for p in submitted]},
        {"key": "responded", "label": "Got a reply", "ids": [p["id"] for p in responded]},
        {"key": "assessment", "label": "Assessment", "ids": reached("assessment")},
        {"key": "interview", "label": "Interview", "ids": reached("interview", "offer")},
        {"key": "offer", "label": "Offer", "ids": reached("offer")},
    ]
    for step in funnel:
        step["count"] = len(step["ids"])
        step["rate"] = _rate(step["count"], len(submitted))

    # -- companies -----------------------------------------------------------
    by_company: dict[str, list[dict]] = defaultdict(list)
    for p in profiles:
        by_company[p["company_key"]].append(p)
    stage_order = ["applied", "assessment", "interview", "offer"]
    companies = []
    for group in by_company.values():
        latest = max(group, key=lambda p: p["last_event_at"])
        best = max(
            (k for p in group for k in p["kinds"] if k in stage_order),
            key=stage_order.index,
            default=None,
        )
        waits = [p["days_to_response"] for p in group if p["days_to_response"] is not None]
        companies.append(
            {
                "company": latest["company"],
                "applications": len(group),
                "responded": sum(p["responded"] for p in group),
                "rejected": sum("rejected" in p["kinds"] for p in group),
                "best_stage": best,
                "best_stage_label": STATUS_LABELS.get(best, "") if best else "",
                "status": latest["status"],
                "days_to_response": round(median(waits)) if waits else None,
                "last_activity": latest["last_event_at"].isoformat(),
                "application_ids": [p["id"] for p in group],
            }
        )
    companies.sort(key=lambda c: (-c["applications"], -c["responded"], c["company"].lower()))
    timed = sorted(
        (c for c in companies if c["days_to_response"] is not None),
        key=lambda c: (c["days_to_response"], c["company"].lower()),
    )

    # -- timing ----------------------------------------------------------------
    waits = [p["days_to_response"] for p in submitted if p["days_to_response"] is not None]
    buckets = [("Under 3 days", 0, 2), ("3–7 days", 3, 7), ("1–2 weeks", 8, 14), ("2–4 weeks", 15, 28), ("Over 4 weeks", 29, None)]
    histogram = []
    for label, low, high in buckets:
        ids = [
            p["id"]
            for p in submitted
            if p["days_to_response"] is not None
            and low <= p["days_to_response"]
            and (high is None or p["days_to_response"] <= high)
        ]
        histogram.append({"label": label, "count": len(ids), "ids": ids})

    # -- channels and roles ------------------------------------------------------
    def rates(key_of, population):
        groups: dict[str, list[dict]] = defaultdict(list)
        for p in population:
            groups[key_of(p)].append(p)
        rows = []
        for name, group in groups.items():
            sent = [p for p in group if p["submitted"]]
            rows.append(
                {
                    "name": name,
                    "count": len(group),
                    "submitted": len(sent),
                    "responded": sum(p["responded"] for p in sent),
                    "rate": _rate(sum(p["responded"] for p in sent), len(sent)),
                    "ids": [p["id"] for p in group],
                }
            )
        return sorted(rows, key=lambda r: (-r["count"], r["name"]))

    channels = rates(lambda p: p["channel"], submitted)
    by_id = {a.id: a for a in applications}
    labels = categorizer.map([a.role for a in applications]) if categorizer else {}
    roles = rates(
        lambda p: labels.get(by_id[p["id"]].role) or role_category(by_id[p["id"]].role),
        profiles,
    )

    # -- rejections --------------------------------------------------------------
    rejected = [p for p in profiles if p["rejection"]]
    reasons: dict[str, list[int]] = defaultdict(list)
    for p in rejected:
        reasons[p["rejection"][1]].append(p["id"])
    rejections = {
        "total": len(rejected),
        "quick": sum(p["quick_rejection"] for p in rejected),
        "quick_ids": [p["id"] for p in rejected if p["quick_rejection"]],
        "reasons": [
            {"label": label, "count": len(ids), "ids": ids}
            for label, ids in sorted(reasons.items(), key=lambda r: (-len(r[1]), r[0]))
        ],
    }

    tested = [p for p in submitted if "assessment" in p["kinds"]]
    passed = [p for p in tested if p["passed_assessment"]]
    ghosted_ids = [p["id"] for p in submitted if p["status"] == "ghosted"]
    ghosted = len(ghosted_ids)
    first = min((p["first_seen"] for p in profiles), default=None)
    summary = {
        "applications": len(profiles),
        "companies": len(companies),
        "submitted": len(submitted),
        "responded": len(responded),
        "response_rate": _rate(len(responded), len(submitted)),
        "interviews": funnel[3]["count"],
        "offers": funnel[4]["count"],
        "ghosted": ghosted,
        "ghost_rate": _rate(ghosted, len(submitted)),
        "median_days_to_response": round(median(waits)) if waits else None,
        "measured_responses": len(waits),
        "started": first.date().isoformat() if first else None,
        "weeks": max(1, ((now - first).days // 7) + 1) if first else 0,
        "ghost_after_days": store.ghost_after_days,
        "ghosted_ids": ghosted_ids,
        "interview_rate": _rate(funnel[3]["count"], len(submitted)),
        "assessments": len(tested),
        "assessments_passed": len(passed),
        "assessment_pass_rate": _rate(len(passed), len(tested)),
        "assessment_ids": [p["id"] for p in tested],
        "assessment_passed_ids": [p["id"] for p in passed],
        "repeat_companies": sum(
            1 for group in by_company.values() if len(group) > 1
        ),
        # Slowest first, so the popup answers "who kept me waiting".
        "measured": [
            {"id": p["id"], "days": p["days_to_response"]}
            for p in sorted(submitted, key=lambda p: -(p["days_to_response"] or 0))
            if p["days_to_response"] is not None
        ],
    }
    return {
        "summary": summary,
        "funnel": funnel,
        "companies": companies,
        "fastest": timed[:5],
        "slowest": timed[::-1][:5] if len(timed) > 5 else [],
        "response_times": histogram,
        "channels": channels,
        "roles": roles,
        "rejections": rejections,
        "highlights": highlights(summary, channels, companies, rejections, funnel),
        "as_of": now.date().isoformat() if as_of else None,
    }


def highlights(summary, channels, companies, rejections, funnel) -> list[str]:
    """A few plain sentences worth reading before the charts."""
    lines = []
    usable = [c for c in channels if c["submitted"] >= 5]
    if len(usable) >= 2:
        best = max(usable, key=lambda c: c["rate"])
        worst = min(usable, key=lambda c: c["rate"])
        if best["rate"] - worst["rate"] >= 10:
            lines.append(
                f"{best['name']} applications get a reply {best['rate']}% of the time, "
                f"against {worst['rate']}% through {worst['name']}."
            )
    if summary["median_days_to_response"] is not None and summary["measured_responses"] >= 3:
        lines.append(
            f"Employers that reply usually do so within {summary['median_days_to_response']} days."
        )
    if summary["ghosted"]:
        lines.append(
            f"{summary['ghosted']} application{'s have' if summary['ghosted'] != 1 else ' has'} "
            f"had no reply for over {summary['ghost_after_days']} days."
        )
    if rejections["total"] >= 3 and rejections["quick"]:
        lines.append(
            f"{rejections['quick']} of {rejections['total']} rejections arrived within "
            f"{QUICK_REJECTION_DAYS} days, which usually means automated screening."
        )
    if summary["assessments"] >= 3:
        lines.append(
            f"{summary['assessments_passed']} of {summary['assessments']} assessments "
            f"led to an interview ({summary['assessment_pass_rate']}%)."
        )
    interviews, offers = funnel[3]["count"], funnel[4]["count"]
    if interviews >= 3 and offers:
        lines.append(f"{_rate(offers, interviews)}% of interviews led to an offer so far.")
    if companies and companies[0]["applications"] >= 2:
        top = companies[0]
        lines.append(f"You applied to {top['company']} most often ({top['applications']} roles).")
    return lines


# -- upcoming and digest -------------------------------------------------------
def upcoming(store, category=None, as_of=None) -> list[dict]:
    """Interviews and assessment deadlines from today on, soonest first."""
    today = (as_of or datetime.now(timezone.utc)).date().isoformat()
    where = ["e.details IS NOT NULL"]
    params: list = []
    if category:
        where.append("a.category = ?")
        params.append(category)
    if as_of:
        where.append("e.event_date < ?")
        params.append(_to_iso(as_of))
    items = []
    for row in store.conn.execute(
        f"SELECT e.id, e.application_id, e.kind, e.details, e.thread_id, a.company, a.role "
        f"FROM events e JOIN applications a ON a.id = e.application_id WHERE {' AND '.join(where)}",
        params,
    ):
        details = json.loads(row["details"])
        stamp = details.get("when") or details.get("deadline")
        if not stamp or stamp[:10] < today:
            continue
        items.append(
            {
                "event_id": row["id"],
                "application_id": row["application_id"],
                "company": row["company"],
                "role": row["role"],
                "kind": "interview" if details.get("when") else "deadline",
                "at": stamp,
                "link": details.get("link"),
                "format": details.get("format"),
                "gmail_url": gmail_thread_url(row["thread_id"]),
            }
        )
    return sorted(items, key=lambda i: i["at"])


def digest(store, since: datetime, category=None) -> dict:
    """What arrived since a moment: new events by kind, and what is coming up."""
    where, params = ["e.event_date >= ?"], [_to_iso(since)]
    if category:
        where.append("a.category = ?")
        params.append(category)
    rows = store.conn.execute(
        f"SELECT e.application_id, e.kind, e.event_date, e.subject, a.company, a.role "
        f"FROM events e JOIN applications a ON a.id = e.application_id "
        f"WHERE {' AND '.join(where)} ORDER BY e.event_date DESC",
        params,
    ).fetchall()
    counts = Counter(r["kind"] for r in rows)
    return {
        "since": since.isoformat(),
        "total": len(rows),
        "counts": {k: counts[k] for k in STATUS_LABELS if counts[k]},
        "events": [
            {
                "application_id": r["application_id"],
                "kind": r["kind"],
                "label": STATUS_LABELS.get(r["kind"], r["kind"]),
                "date": r["event_date"],
                "company": r["company"],
                "role": r["role"],
                "subject": r["subject"],
            }
            for r in rows[:20]
        ],
        "upcoming": upcoming(store, category)[:10],
    }


# -- one application: people and history ----------------------------------------
def contacts(events) -> list[dict]:
    """People who wrote about this application, not platforms or no-reply robots."""
    people: dict[str, dict] = {}
    for event in events:
        if event.source == "manual" or "outbound application" in event.matched:
            continue
        sender = event.sender or ""
        name = sender[: sender.rfind("<")].strip().strip('"') if "<" in sender else ""
        address = (sender[sender.rfind("<") + 1 :].rstrip(">") if "<" in sender else sender).strip().lower()
        if "@" not in address:
            continue
        local = address.split("@")[0]
        if _AUTOMATED_SENDER.match(local) or _root_domain(_domain_of(address)) in ATS_DOMAINS:
            continue
        person = people.setdefault(
            address, {"name": name, "email": address, "messages": 0, "last": None}
        )
        person["name"] = person["name"] or name
        person["messages"] += 1
        person["last"] = event.event_date.isoformat()
    return sorted(people.values(), key=lambda p: p["last"], reverse=True)


def company_history(store, application) -> dict:
    """Other applications at the same company, and whether a reapply cooldown applies."""
    key = normalize_company(application.company)
    others = [
        a
        for a in store.list_applications(category=application.category)
        if a.id != application.id and normalize_company(a.company) == key
    ]
    return {
        "applications": [
            {
                "id": a.id,
                "role": a.role,
                "status": a.status,
                "first_seen": a.first_seen.isoformat(),
                "last_event_at": a.last_event_at.isoformat(),
            }
            for a in others
        ],
        "cooldown": cooldown(others + [application], exclude=application.id),
    }


def cooldown(applications, exclude=None, now=None) -> dict | None:
    """The most recent rejection at a company, if it is under six months old."""
    now = now or datetime.now(timezone.utc)
    rejected = [
        a for a in applications if a.status == "rejected" and a.id != exclude
    ]
    if not rejected:
        return None
    latest = max(rejected, key=lambda a: a.last_event_at)
    days = (now - latest.last_event_at).days
    if days >= COOLDOWN_DAYS:
        return None
    return {
        "application_id": latest.id,
        "role": latest.role,
        "rejected_days_ago": days,
        "until": (latest.last_event_at + timedelta(days=COOLDOWN_DAYS)).date().isoformat(),
    }


def lookup(store, needle: str) -> list[dict]:
    """Have I applied here before? Matches grouped by company."""
    groups: dict[str, list] = defaultdict(list)
    for a in store.find_application_by_name(needle):
        groups[normalize_company(a.company)].append(a)
    results = []
    for group in groups.values():
        results.append(
            {
                "company": group[0].company,
                "applications": [
                    {
                        "id": a.id,
                        "role": a.role,
                        "status": a.status,
                        "status_label": a.status_label,
                        "category": a.category,
                        "first_seen": a.first_seen.isoformat(),
                        "last_event_at": a.last_event_at.isoformat(),
                    }
                    for a in group
                ],
                "cooldown": cooldown(group),
            }
        )
    return results


# -- follow-up drafts ------------------------------------------------------------
def _template_draft(application, events, people) -> str:
    role = application.role or "the open position"
    greeting = people[0]["name"].split()[0] if people and people[0]["name"] else "Hiring team"
    interview = next((e for e in reversed(events) if e.kind == "interview"), None)
    first = f"{events[0].event_date:%B} {events[0].event_date.day}" if events else "recently"
    if interview:
        subject = f"Thank you, and following up on the {role} interview"
        body = (
            f"Thank you again for taking the time to speak with me about the {role} "
            f"role at {application.company}. I enjoyed our conversation and remain very "
            "interested in the position.\n\nCould you share an update on the next steps "
            "or the timeline? I'm happy to provide anything else that would help."
        )
    else:
        subject = f"Following up on my {role} application"
        body = (
            f"I applied for the {role} role at {application.company} on {first} and "
            "wanted to follow up. I'm still very interested in the position and would "
            "welcome the chance to discuss how I could contribute.\n\nIs there an update "
            "on the hiring timeline? I'm happy to share anything else that would help."
        )
    return f"Subject: {subject}\n\nHi {greeting},\n\n{body}\n\nBest regards,\n"


DRAFT_SYSTEM = (
    "You write short, polite follow-up emails for job applications. Use only the "
    "facts given; never invent names, dates, or achievements. Write in the language "
    "of the email subjects. Reply with the email only, starting with a 'Subject:' "
    "line, at most 120 words, and end with 'Best regards,' and no name."
)


def follow_up_draft(application, events, llm=None) -> dict:
    """A follow-up to copy into your mail client. jobtrack never sends mail.

    With a model, only the company, role, status, contact names, and the dates,
    kinds, and subjects of the timeline are shared — never email bodies.
    """
    people = contacts(events)
    template = _template_draft(application, events, people)
    if llm is None:
        return {"text": template, "source": "template"}
    timeline = "\n".join(
        f"{e.event_date.date().isoformat()} | {e.kind} | {e.subject[:150]}" for e in events
    )
    prompt = (
        f"Company: {application.company}\nRole: {application.role or 'not stated'}\n"
        f"Current status: {application.status_label}\n"
        f"Contact: {people[0]['name'] if people and people[0]['name'] else 'unknown'}\n"
        f"Timeline:\n{timeline}"
    )
    try:
        text = llm.complete(DRAFT_SYSTEM, prompt).strip()
    except Exception as exc:  # any provider failure falls back to the template
        return {"text": template, "source": "template", "error": f"{type(exc).__name__}: {exc}"}
    if not text:
        return {"text": template, "source": "template"}
    return {"text": text + "\n", "source": "model"}


# -- retrospective -----------------------------------------------------------------
def report_markdown(data: dict, anonymize: bool = False) -> str:
    """The search so far, as Markdown to keep or share."""
    s = data["summary"]
    names: dict[str, str] = {}

    def name(company: str) -> str:
        if not anonymize:
            return company
        if company not in names:
            index = len(names)
            names[company] = "Company " + (
                chr(65 + index) if index < 26 else str(index + 1)
            )
        return names[company]

    lines = [
        "# Job search retrospective",
        "",
        f"Since {s['started'] or '—'} · {s['weeks']} weeks",
        "",
        f"- **{s['applications']}** applications to **{s['companies']}** companies",
        f"- **{s['responded']}** replies ({s['response_rate']}% of {s['submitted']} submitted)",
        f"- **{s['interviews']}** reached an interview, **{s['offers']}** offer{'s' if s['offers'] != 1 else ''}",
    ]
    if s["assessments"]:
        lines.append(
            f"- **{s['assessments_passed']}** of {s['assessments']} assessments led to an interview ({s['assessment_pass_rate']}%)"
        )
    if s["median_days_to_response"] is not None:
        lines.append(f"- Typical wait for a reply: **{s['median_days_to_response']} days**")
    if s["ghosted"]:
        lines.append(f"- **{s['ghosted']}** never replied ({s['ghost_rate']}%)")
    if data["highlights"]:
        lines += ["", "## Highlights", ""] + [
            f"- {line}" for line in data["highlights"] if not anonymize or "most often" not in line
        ]
    lines += ["", "## Funnel", "", "| Stage | Applications | Share |", "| --- | ---: | ---: |"]
    lines += [f"| {f['label']} | {f['count']} | {f['rate']}% |" for f in data["funnel"]]
    if data["channels"]:
        lines += ["", "## Channels", "", "| Channel | Submitted | Reply rate |", "| --- | ---: | ---: |"]
        lines += [f"| {c['name']} | {c['submitted']} | {c['rate']}% |" for c in data["channels"]]
    if data["rejections"]["reasons"]:
        lines += ["", "## Rejections", ""]
        lines += [f"- {r['label']}: {r['count']}" for r in data["rejections"]["reasons"]]
    if data["companies"]:
        lines += ["", "## Companies", "", "| Company | Applications | Replies | Furthest stage |", "| --- | ---: | ---: | --- |"]
        lines += [
            f"| {name(c['company'])} | {c['applications']} | {c['responded']} | {c['best_stage_label'] or '—'} |"
            for c in data["companies"][:15]
        ]
    return "\n".join(lines) + "\n"
