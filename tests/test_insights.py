import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jobtrack import cli, insights
from jobtrack.details import calendar_file, extract_details
from jobtrack.models import Classification
from jobtrack.schedule import ScheduleSpec
from jobtrack.store import Store
from jobtrack.web import create_app
from tests.helpers import make_message

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SENT = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def record(store, kind, company, role=None, days_ago=0, sender=None, mid=None, **extra):
    mid = mid or f"{company}-{kind}-{days_ago}"
    return store.record(
        make_message(
            extra.pop("subject", f"{kind} from {company}"),
            extra.pop("body", ""),
            sender=sender or f"Recruiter <talent@{company.lower()}.com>",
            message_id=mid,
            thread_id=mid,
            date=NOW - timedelta(days=days_ago),
        ),
        Classification(kind=kind, score=12, company=company, role=role),
    )[1]


# -- details ---------------------------------------------------------------
def test_interview_time_link_and_format():
    msg = make_message(
        "Interview invitation",
        "Your video interview is on November 12, 2026 at 2:30 PM. "
        "Join: https://zoom.us/j/123456?pwd=abc.",
        date=SENT,
    )
    details = extract_details(msg, "interview")
    assert details == {
        "when": "2026-11-12T14:30",
        "link": "https://zoom.us/j/123456?pwd=abc",
        "link_kind": "meeting",
        "format": "video",
    }


def test_turkish_date_and_relative_deadline():
    msg = make_message(
        "Mülakat daveti",
        "Görüşmemiz 12 Kasım saat 14:00'te ofisimizde olacaktır.",
        date=SENT,
    )
    assert extract_details(msg, "interview") == {
        "when": "2026-11-12T14:00",
        "format": "onsite",
    }
    test = make_message(
        "Coding challenge",
        "Please complete it within 72 hours: https://www.hackerrank.com/test/xyz",
        date=SENT,
    )
    assert extract_details(test, "assessment") == {
        "deadline": "2026-11-05T09:00",
        "link": "https://www.hackerrank.com/test/xyz",
        "link_kind": "assessment",
    }


def test_weekday_needs_a_time_and_other_kinds_are_skipped():
    tuesday = make_message("Chat", "Are you free Tuesday at 3pm?", date=SENT)
    assert extract_details(tuesday, "interview") == {"when": "2026-11-03T15:00"}
    vague = make_message("Chat", "Let's talk on Tuesday.", date=SENT)
    assert extract_details(vague, "interview") is None
    assert extract_details(tuesday, "rejected") is None


def test_calendar_file_uses_floating_time_and_escapes():
    text = calendar_file(
        7, "interview", {"when": "2026-11-12T14:30", "link": "https://meet.google.com/a"},
        "Acme, Inc", "Engineer",
    )
    assert "DTSTART:20261112T143000" in text
    assert "DTEND:20261112T153000" in text
    assert "SUMMARY:Interview · Acme\\, Inc (Engineer)" in text
    assert text.endswith("END:VCALENDAR\r\n")
    assert calendar_file(1, "interview", {"format": "video"}, "A", None) is None


# -- store: ghosting and manual applications ---------------------------------
def test_ghosting_is_derived_and_respects_corrections(tmp_path):
    with Store(tmp_path / "a.db", ghost_after_days=30) as store:
        old = record(store, "applied", "Quiet", days_ago=40)
        recent = record(store, "applied", "Fresh", days_ago=5)
        fixed = record(store, "applied", "Mine", days_ago=40)
        store.update_application(fixed, {"status_override": "applied"})
        statuses = {a.id: a.status for a in store.list_applications()}
        assert statuses == {old: "ghosted", recent: "applied", fixed: "applied"}
        assert [a.id for a in store.list_applications(statuses=["ghosted"])] == [old]
        assert store.status_counts()["ghosted"] == 1
        # The stored column is untouched, so a reply simply takes over.
        record(store, "interview", "Quiet", days_ago=1)
        assert store.get_application(old).status == "interview"
    with Store(tmp_path / "a.db", ghost_after_days=0) as store:
        assert store.get_application(recent).status == "applied"


def test_manual_application_is_joined_by_later_mail(tmp_path):
    with Store(tmp_path / "a.db", ghost_after_days=0) as store:
        app_id = store.add_application(
            "Initech", "Data Engineer", applied_on=NOW - timedelta(days=3), notes="careers page"
        )
        assert record(store, "interview", "Initech", "Data Engineer") == app_id
        app = store.get_application(app_id)
        assert app.status == "interview" and app.notes == "careers page"
        assert store.get_events(app_id)[0].source == "manual"
        with pytest.raises(sqlite3.IntegrityError):
            store.add_application("Initech", "Data Engineer")
        with pytest.raises(ValueError):
            store.add_application("  ")


# -- insights ------------------------------------------------------------------
@pytest.fixture
def search(tmp_path):
    path = tmp_path / "s.db"
    with Store(path, ghost_after_days=30) as store:
        # LinkedIn: applied, then interview, then offer.
        record(store, "applied", "Acme", "Backend", 30, sender="LinkedIn <jobs-noreply@linkedin.com>")
        record(store, "interview", "Acme", "Backend", 20)
        record(store, "offer", "Acme", "Backend", 5)
        # Acme again, a second role, rejected fast without a reason.
        record(store, "applied", "Acme", "Platform", 12, sender="Greenhouse <no-reply@greenhouse.io>")
        record(store, "rejected", "Acme", "Platform", 11, sender="Greenhouse <no-reply@greenhouse.io>")
        # Silent for 45 days.
        record(store, "applied", "Hooli", "Backend", 45, sender="LinkedIn <jobs-noreply@linkedin.com>")
        # Known only from a rejection that names other candidates.
        record(
            store, "rejected", "Globex", "Analyst", 3,
            body="We decided to move forward with other candidates.",
        )
    return path


def test_insights_funnel_companies_channels_and_rejections(search):
    with Store(search, ghost_after_days=30) as store:
        data = insights.insights(store)
    s = data["summary"]
    assert (s["applications"], s["companies"], s["submitted"], s["responded"]) == (4, 3, 4, 3)
    assert (s["interviews"], s["offers"], s["ghosted"]) == (1, 1, 1)
    # Globex has no earlier email, so only Acme's two replies are timed.
    assert s["measured_responses"] == 2 and s["median_days_to_response"] == 6
    assert [f["count"] for f in data["funnel"]] == [4, 3, 0, 1, 1]
    top = data["companies"][0]
    assert (top["company"], top["applications"], top["responded"], top["best_stage"]) == (
        "Acme", 2, 2, "offer",
    )
    channels = {c["name"]: c for c in data["channels"]}
    assert channels["LinkedIn"]["submitted"] == 2 and channels["LinkedIn"]["rate"] == 50
    assert channels["Applicant tracking system"]["rate"] == 100
    reasons = {r["label"]: r["count"] for r in data["rejections"]["reasons"]}
    assert reasons == {"No reason given": 1, "Went with other candidates": 1}
    assert data["rejections"]["quick"] == 2
    assert data["summary"]["ghosted_ids"] and data["funnel"][4]["ids"]
    assert data["summary"]["assessments"] == 0


def test_assessment_pass_rate_needs_an_interview_after_the_test(tmp_path):
    with Store(tmp_path / "t.db", ghost_after_days=0) as store:
        passed = record(store, "assessment", "Acme", "Backend", 20)
        record(store, "interview", "Acme", "Backend", 10)
        record(store, "assessment", "Hooli", "Backend", 15)
        record(store, "rejected", "Hooli", "Backend", 5)
        data = insights.insights(store)
    s = data["summary"]
    assert (s["assessments"], s["assessments_passed"], s["assessment_pass_rate"]) == (2, 1, 50)
    assert s["assessment_passed_ids"] == [passed]
    assert "1** of 2 assessments" in insights.report_markdown(data)


def test_report_can_hide_company_names(search):
    with Store(search, ghost_after_days=30) as store:
        data = insights.insights(store)
    assert "Acme" in insights.report_markdown(data)
    hidden = insights.report_markdown(data, anonymize=True)
    assert "Acme" not in hidden and "Hooli" not in hidden and "Company A" in hidden


def test_lookup_and_cooldown(search):
    with Store(search, ghost_after_days=30) as store:
        results = insights.lookup(store, "acme")
    assert len(results) == 1 and len(results[0]["applications"]) == 2
    assert results[0]["cooldown"]["rejected_days_ago"] == 11


def test_upcoming_digest_and_contacts(tmp_path):
    with Store(tmp_path / "d.db", ghost_after_days=0) as store:
        app_id = record(
            store, "interview", "Acme", "Backend", 1, mid="iv",
            sender="Jane Doe <jane@acme.com>",
        )
        soon = (NOW + timedelta(days=2)).date().isoformat()
        store.set_event_details("iv", {"when": f"{soon}T10:00"})
        record(store, "rejected", "Old", days_ago=20)
        coming = insights.upcoming(store)
        assert [(c["application_id"], c["kind"]) for c in coming] == [(app_id, "interview")]
        digest = insights.digest(store, NOW - timedelta(days=7))
        assert digest["counts"] == {"interview": 1} and digest["upcoming"]
        record(store, "interview", "Acme", "Backend", 0, mid="bot",
               sender="LinkedIn <messages-noreply@linkedin.com>")
        people = insights.contacts(store.get_events(app_id))
        assert [p["email"] for p in people] == ["jane@acme.com"]
        assert people[0]["name"] == "Jane Doe"


def test_follow_up_draft_template_and_model_fallback(tmp_path):
    with Store(tmp_path / "f.db", ghost_after_days=0) as store:
        app_id = record(store, "applied", "Acme", "Backend", 10, sender="Jane Doe <jane@acme.com>")
        app, events = store.get_application(app_id), store.get_events(app_id)
    draft = insights.follow_up_draft(app, events)
    assert draft["source"] == "template"
    assert draft["text"].startswith("Subject: Following up on my Backend application")
    assert "Hi Jane," in draft["text"]

    class Broken:
        def complete(self, system, user):
            raise RuntimeError("offline")

    class Model:
        def complete(self, system, user):
            assert "Acme" in user and "jane@" not in user
            return "Subject: Hello\n\nHi Jane"

    assert insights.follow_up_draft(app, events, Broken())["source"] == "template"
    assert insights.follow_up_draft(app, events, Model())["source"] == "model"


# -- web -------------------------------------------------------------------------
def test_web_endpoints(search):
    client = TestClient(create_app(db_path=search, llm_mode="off"))
    created = client.post(
        "/api/applications",
        json={"company": "Initech", "role": "SRE", "applied_on": "2026-10-01"},
    )
    assert created.status_code == 201
    app_id = created.json()["application"]["id"]
    assert client.post("/api/applications", json={"company": "Initech", "role": "SRE"}).status_code == 409

    assert client.get("/api/insights?category=employment").json()["summary"]["applications"] >= 5
    assert client.get("/api/lookup?q=acme").json()["results"][0]["company"] == "Acme"
    report = client.get("/api/report?anonymize=true")
    assert report.status_code == 200 and "Acme" not in report.text
    assert client.get("/api/digest?since=2020-01-01T00:00:00Z").json()["total"] >= 7

    draft = client.post(f"/api/applications/{app_id}/follow-up-draft").json()
    assert draft["source"] == "template"

    acme = insights.lookup(Store(search, ghost_after_days=0), "acme")[0]["applications"][0]["id"]
    detail = client.get(f"/api/applications/{acme}").json()
    assert detail["company_history"]["applications"]
    assert detail["contacts"][0]["email"] == "talent@acme.com"
    event_id = detail["events"][0]["id"]
    assert client.get(f"/api/events/{event_id}/calendar.ics").status_code == 404

    goals = [
        {"metric": "applications", "period": "week", "target": 3},
        {"metric": "interviews", "period": "month", "target": 2},
    ]
    saved = client.put("/api/settings", json={"ghost_after_days": 10, "goals": goals}).json()
    assert saved["ghost_after_days"] == 10 and saved["goals"] == goals
    progress = client.get("/api/overview").json()["goals"]
    assert [(g["label"], g["target"]) for g in progress] == [
        ("Applications per week", 3),
        ("Interviews per month", 2),
    ]
    bad = {"metric": "naps", "period": "week", "target": 1}
    assert client.put("/api/settings", json={"goals": [bad]}).status_code == 422


def test_calendar_endpoint_serves_ics(tmp_path):
    with Store(tmp_path / "c.db", ghost_after_days=0) as store:
        app_id = record(store, "interview", "Acme", mid="iv")
        store.set_event_details("iv", {"when": "2026-11-12T14:30"})
        event_id = store.get_events(app_id)[0].id
    client = TestClient(create_app(db_path=tmp_path / "c.db", llm_mode="off"))
    response = client.get(f"/api/events/{event_id}/calendar.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "DTSTART:20261112T143000" in response.text


# -- cli -----------------------------------------------------------------------------
def test_cli_add_check_and_report(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert cli.main(["--db", db, "add", "Initech", "--role", "SRE", "--date", "2026-10-01"]) == 0
    assert cli.main(["--db", db, "add", "Initech", "--role", "SRE"]) == 1
    assert cli.main(["--db", db, "check", "initech"]) == 0
    assert "SRE" in capsys.readouterr().out
    assert cli.main(["--db", db, "report", "--anonymize"]) == 0
    out = capsys.readouterr().out
    assert "Job search retrospective" in out and "Initech" not in out


def test_notifications_summarize_and_schedule_asks_for_them():
    news = [{"kind": "interview", "company": "Acme"}, {"kind": "offer", "company": "Hooli"}]
    assert cli._describe_news(news) == "Interview invite from Acme; Offer from Hooli"
    assert "--notify" in ScheduleSpec(hour=8, minute=0, since=7, db_path="x.db").command()


def test_goal_progress_counts_current_period_and_streak():
    from jobtrack.workspace import goal_progress

    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)  # a Wednesday
    week = lambda n: now - timedelta(weeks=n)  # noqa: E731
    moments = {
        "applications": {1: week(0), 2: week(0), 3: week(1), 4: week(1), 5: week(2), 6: week(3), 7: week(3)},
        "interviews": {1: now, 3: datetime(2026, 10, 5, tzinfo=timezone.utc)},
    }
    weekly, monthly = goal_progress(
        [
            {"metric": "applications", "period": "week", "target": 2},
            {"metric": "interviews", "period": "month", "target": 1},
        ],
        moments,
        now,
    )
    # This week and last week met it; two weeks ago (1) broke the streak.
    assert (weekly["current"], weekly["streak"], sorted(weekly["ids"])) == (2, 2, [1, 2])
    assert (monthly["current"], monthly["streak"], monthly["period_start"]) == (1, 2, "2026-11-01")


def test_legacy_weekly_goal_becomes_a_goal():
    from jobtrack.settings import Settings

    assert Settings.from_dict({"weekly_goal": 5}).goals == [
        {"metric": "applications", "period": "week", "target": 5}
    ]
    assert Settings.from_dict({"goals": [{"metric": "x", "period": "week", "target": 1}]}).goals == []
