#!/usr/bin/env python3
"""Seed a demo database with entirely fictional applications.

Nothing here is derived from a real mailbox: every employer, role, sender, and
snippet is invented, so screenshots taken against this database carry no
personal information.
"""

import random
import sys
from datetime import datetime, timedelta, timezone

from jobtrack.models import Classification
from jobtrack.store import Store

# Fictional employers. None of these are drawn from a real inbox.
EMPLOYERS = [
    ("Northlight Games", "Junior Software Engineer", "employment"),
    ("Brightline", "Backend Engineer", "employment"),
    ("Cobalt Systems", "Full Stack Developer", "employment"),
    ("Quorix", "Data Analyst", "employment"),
    ("Foldwell", "Platform Engineer", "employment"),
    ("Meridian Networks", "Telecommunications Engineer", "employment"),
    ("Lumen Works", "Site Reliability Engineer", "employment"),
    ("Northgate", "Product Designer", "employment"),
    ("Trellis", "Software Engineer, Payments", "employment"),
    ("Halo Robotics", "Controls Engineer", "employment"),
    ("Kestrel Logistics", "Backend Engineer", "employment"),
    ("Brightmoor Energy", "Power Systems Engineer", "employment"),
    ("Kirkby Group", "Back-End Developer", "employment"),
    ("Nordvale", "Optical Networks Engineer", "employment"),
    ("Cardinal Analytics", "ML Engineer", "employment"),
    ("Sandbar", "Web Developer", "employment"),
    ("Maple Systems", "Systems Engineer", "employment"),
    ("Vantage", "Backend Engineer, ClickHouse", "employment"),
    ("Gridworks", "Energy Data Engineer", "employment"),
    ("Kite Mobility", "Platform Engineer", "employment"),
    ("Tamsin", "QA Engineer", "employment"),
    ("Cadence", "Machine Learning Engineer", "employment"),
    ("Evrid", "DevOps Engineer", "employment"),
    ("Solis", "Software Developer, KPI", "employment"),
    ("Aster", "Backend Engineer", "employment"),
    ("Harbor", "Full Stack Developer", "employment"),
    ("Fernwood", "Junior Software Engineer (Node.js)", "employment"),
    ("Sentinel Group", "Software Developer", "employment"),
    ("Halcyon AI", "AI Research Engineer", "employment"),
    ("Redwood Systems", "Software Engineer, SaaS", "employment"),
    ("Kalemci", "Yazılım Geliştirme Uzmanı", "employment"),
    ("Novastudio", "Frontend Developer", "employment"),
    ("Larkfield", "Security Engineer", "employment"),
    ("Perch Analytics", "Data Engineer", "employment"),
    ("Sundial Health", "Software Engineer, Platform", "employment"),
    ("Orrery Labs", "Research Engineer", "employment"),
    ("Foxglove Media", "Senior Backend Engineer", "employment"),
    ("Ironwood Legal", "Software Engineer", "employment"),
    ("Cobblestone Pay", "Payments Engineer", "employment"),
    ("Windrose", "Junior Full Stack Engineer", "employment"),
    # Freelance
    ("Marketlane", "WordPress Developer", "freelance"),
    ("Draft & Co", "Landing Page Developer", "freelance"),
    ("Brightpath Tutoring", "Tutor Platform Admin", "freelance"),
]

SENDERS = {
    "ats": "no-reply@ashbyhq.com",
    "teamtailor": "no-reply@candidates.workablemail.com",
    "greenhouse": "no-reply@us.greenhouse-mail.io",
    "lever": "no-reply@hire.lever.co",
    "recruiter": "Talent Team <talent@example.org>",
}

SUBJECTS = {
    "applied": [
        "Thanks for applying to {company}",
        "We have received your application for {role}",
        "Your application to {company} has been received",
        "{company} — application received",
    ],
    "assessment": [
        "Next steps: take-home assignment at {company}",
        "Your coding challenge at {company}",
        "{company} online assessment invitation",
    ],
    "interview": [
        "Interview scheduling — {role} at {company}",
        "Next steps for your {company} application",
        "Can we speak on Thursday?",
        "{company} interview invitation",
    ],
    "rejected": [
        "Update on your application at {company}",
        "After careful consideration at {company}",
        "Your application to {company}",
    ],
    "offer": [
        "Offer — {role} at {company}",
        "{company} is pleased to offer",
        "Congratulations on your {company} offer",
    ],
    "outreach": [
        "Your profile stood out — {company}",
        "Would you be open to a role at {company}?",
        "{company} talent acquisition",
    ],
}

SNIPPETS = {
    "outreach": [
        "Your profile stood out to our team and we would like to talk.",
        "We came across your background and thought you might be a fit.",
    ],
    "applied": [
        "Thanks for applying. We have received your application and will be in touch.",
        "Your application has been received and is with the hiring team.",
        "We have logged your application. No further action is needed yet.",
    ],
    "assessment": [
        "Please complete the assessment at your convenience before the deadline.",
        "Your take-home exercise is ready. Let us know if anything is unclear.",
        "The online assessment link is below. Aim to finish this week.",
    ],
    "interview": [
        "We would like to schedule time for a conversation with the hiring manager.",
        "Does Thursday or Friday afternoon work for a first call?",
        "Looking forward to speaking with you about the role.",
    ],
    "rejected": [
        "After careful consideration we will not be moving forward at this time.",
        "We have decided to proceed with other candidates for this role.",
        "Thank you for your interest. We wish you well in your search.",
    ],
    "offer": [
        "We are delighted to make you an offer and would like to discuss details.",
        "Congratulations. Attached is the offer letter for the role.",
        "The team was impressed and would like to extend an offer.",
    ],
}


def main(path: str) -> None:
    rng = random.Random(20261008)  # fixed seed: same demo data every run
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    # Most applications land inside the 8-week window the overview charts, with
    # a few older ones so the history looks plausible rather than uniform.
    recent = now - timedelta(days=56)

    with Store(path) as store:
        for company, role, category in EMPLOYERS:
            # Each application follows a plausible lifecycle.
            track = ["outreach"]
            if rng.random() < 0.85:
                track.append("applied")
            if rng.random() < 0.45:
                track.append("assessment")
            if rng.random() < 0.30:
                track.append("interview")
                roll = rng.random()
                if roll < 0.25:
                    track.append("offer")
                elif roll < 0.55:
                    track.append("rejected")
                # else the interview is still open, which is the common case
            elif rng.random() < 0.45:
                track.append("rejected")

            # The volume chart counts applications by the week they were first
            # seen, so spread *first_seen* across the window and compress the
            # gaps until the whole track fits before today.
            start = now - timedelta(
                days=rng.randint(1, 56) if rng.random() < 0.8 else rng.randint(57, 240)
            )
            gaps = [rng.randint(3, 14) for _ in track]
            budget = (now - start).days
            while sum(gaps) > budget and max(gaps) > 1:
                gaps = [max(1, gap - 1) for gap in gaps]
            cursor = start

            thread = f"t-{company.lower().replace(' ', '-')}"
            for index, kind in enumerate(track):
                if index:
                    cursor += timedelta(days=gaps[index])
                # A long track on a very recent start can still overrun today;
                # clamp rather than date an event in the future.
                when = min(cursor, now) - timedelta(hours=rng.randint(0, 6))
                subject = rng.choice(SUBJECTS[kind]).format(company=company, role=role)
                store.record(
                    _message(
                        rng=rng,
                        subject=subject,
                        sender=_sender(company, rng),
                        message_id=f"m-{company[:6]}-{index}",
                        thread_id=thread,
                        when=when,
                        kind=kind,
                    ),
                    Classification(
                        kind=kind,
                        score=rng.uniform(6, 14),
                        company=company,
                        role=role,
                    ),
                )
                store.conn.execute(
                    "UPDATE applications SET category = ? WHERE company = ?",
                    (category, company),
                )
                # Legacy sqlite3 leaves an implicit transaction open after a
                # write, which would break record()'s BEGIN IMMEDIATE.
                store.conn.commit()

        # Notes and follow-ups make the overview's attention panel non-empty.
        for company, role, _ in EMPLOYERS[:12]:
            store.conn.execute(
                "UPDATE applications SET notes = ?, follow_up_on = ? "
                "WHERE company = ? AND follow_up_on IS NULL",
                (
                    "Recruiter mentioned a take-home; check the deadline.",
                    "2026-10-12",
                    company,
                ),
            )
        store.conn.commit()
        store.set_meta("last_sync_at", now.isoformat())
        total = len(store.list_applications())
    print(f"seeded {total} applications into {path}")


def _sender(company: str, rng: random.Random) -> str:
    domain = company.lower().replace(" ", "").replace(",", "").replace("&", "and")
    key = rng.choice(["ats", "teamtailor", "greenhouse", "lever", "recruiter"])
    if key == "recruiter":
        return f"Talent Team <talent@{domain}.example>"
    return SENDERS[key]


def _message(rng, subject, sender, message_id, thread_id, when, kind):
    from jobtrack.models import EmailMessage

    return EmailMessage(
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender=sender,
        date=when,
        snippet=rng.choice(SNIPPETS[kind]),
        body="",
    )


if __name__ == "__main__":
    main(sys.argv[1])