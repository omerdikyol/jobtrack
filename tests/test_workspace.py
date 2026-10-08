"""Regression proofs for the redesigned workspace and import boundary."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jobtrack.analyze import Analyzer, build_analyzer
from jobtrack.models import Classification
from jobtrack.store import SCHEMA, Store, SyncBusyError
from jobtrack.sync import run_sync
from jobtrack.web import SyncRunner, create_app
from jobtrack.workspace import overview
from tests.helpers import make_message

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def event(
    store,
    message_id="m1",
    thread_id="t1",
    company="Acme",
    role="Engineer",
    kind="applied",
    date=BASE,
):
    return store.record(
        make_message(
            "Application update", message_id=message_id, thread_id=thread_id, date=date
        ),
        Classification(kind=kind, score=12, company=company, role=role),
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "tracker.db") as db:
        yield db


def test_migration_preserves_legacy_history_and_is_repeatable(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO applications VALUES(1,'Acme','acme','Engineer','engineer','applied',?,?)",
        (BASE.isoformat(), BASE.isoformat()),
    )
    conn.execute(
        "INSERT INTO events(application_id,message_id,kind,event_date) VALUES(1,'legacy','applied',?)",
        (BASE.isoformat(),),
    )
    conn.commit()
    conn.close()
    for _ in range(2):
        with Store(path) as db:
            assert db.get_application(1).company == "Acme"
            assert db.total_events() == 1
            assert db.get_events(1)[0].message_id == "legacy"
            assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 3
            assert db.get_application(1).notes == ""


def test_migration_refuses_future_schema(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer version"):
        Store(path)


def test_different_known_roles_stay_separate(store):
    event(store)
    event(store, message_id="m2", thread_id="t2", role="Designer")
    assert {a.role for a in store.list_applications()} == {"Engineer", "Designer"}


def test_empty_threads_do_not_merge_unrelated_companies(store):
    event(store, thread_id="")
    event(store, message_id="m2", thread_id="", company="Globex")
    assert len(store.list_applications()) == 2


def test_unknown_companies_do_not_merge_and_recheck_keeps_id(store):
    _, first = event(store, company=None, thread_id="")
    event(store, message_id="m2", thread_id="", company=None)
    assert len(store.list_applications()) == 2
    result, app_id = store.record(
        make_message("Update", message_id="m1", thread_id=""),
        Classification("interview", 12),
        replace=True,
    )
    assert app_id == first
    assert store.get_application(first).status == "interview"


def test_roleless_followup_does_not_choose_arbitrary_role(store):
    event(store)
    event(store, message_id="m2", thread_id="t2", role="Designer")
    event(store, message_id="m3", thread_id="t3", role=None, kind="interview")
    assert len(store.list_applications()) == 3
    assert store.get_application(1).status == "applied"


def test_unambiguous_application_is_enriched_when_role_arrives(store):
    _, app_id = event(store, role=None)
    _, match = event(store, message_id="m2", thread_id="t2", role="Engineer")
    assert match == app_id
    assert store.get_application(app_id).role == "Engineer"


def test_replacement_rolls_back_if_insert_fails(store):
    event(store)
    store.conn.execute(
        "CREATE TRIGGER reject_replacement BEFORE INSERT ON events WHEN NEW.kind = 'interview' BEGIN SELECT RAISE(ABORT, 'failed insert'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.record(
            make_message("Update", message_id="m1", thread_id="t1"),
            Classification("interview", 12, company="Acme"),
            replace=True,
        )
    assert store.get_events(1)[0].kind == "applied"
    assert store.total_events() == 1


def test_notes_and_status_override_survive_sync_and_reset_to_automatic(store):
    _, app_id = event(store)
    store.update_application(
        app_id,
        {
            "notes": "Ask about the team",
            "follow_up_on": "2026-10-08",
            "status_override": "interview",
        },
    )
    event(store, message_id="m2", kind="rejected")
    app = store.get_application(app_id)
    assert app.notes == "Ask about the team" and app.follow_up_on == "2026-10-08"
    assert app.status == "interview"
    historical = store.get_application(app_id, as_of=BASE + timedelta(days=1))
    assert historical.status == "rejected" and historical.status_override is None
    store.update_application(app_id, {"status_override": None})
    assert store.get_application(app_id).status == "rejected"


def test_sync_lock_is_shared_across_store_connections(store):
    run_id = store.begin_sync("query")
    with Store(store.path) as other:
        with pytest.raises(SyncBusyError):
            other.begin_sync("another")
        assert other.sync_history()[0]["status"] == "running"
    store.finish_sync(run_id, {"scanned": 3})
    assert store.begin_sync("another") > run_id


def test_dead_owner_is_recorded_as_interrupted(store, monkeypatch):
    store.begin_sync("query")

    def dead(pid, signal):
        raise ProcessLookupError()

    monkeypatch.setattr("jobtrack.store.os.kill", dead)
    run = store.sync_history()[0]
    assert run["status"] == "error" and "interrupted" in run["error"]
    store.begin_sync("retry")


def test_finished_jobs_survive_runner_restart(store):
    run_id = store.begin_sync("query")
    store.finish_sync(run_id, {"scanned": 12, "created": 4})
    fresh = SyncRunner(str(store.path)).snapshot()
    assert fresh["status"] == "done"
    assert fresh["summary"]["created"] == 4


class IdClient:
    def __init__(self, messages):
        self.messages = {m.message_id: m for m in messages}
        self.fetched = []

    def iter_message_ids(self, query, limit=0):
        return iter(list(self.messages)[: limit or None])

    def get_message(self, message_id):
        self.fetched.append(message_id)
        return self.messages[message_id]


def test_repeat_sync_does_not_download_known_or_ignored_bodies(store):
    client = IdClient(
        [
            make_message("Thanks for applying to Acme", message_id="job"),
            make_message(
                "Your receipt", body="Order confirmation", message_id="receipt"
            ),
        ]
    )
    analyzer = Analyzer(None, "off")
    first = run_sync(store, client, analyzer, query="x")
    assert first.created == 1 and first.skipped == 1
    client.fetched.clear()
    second = run_sync(store, client, Analyzer(None, "off"), query="x")
    assert second.duplicate == 2
    assert client.fetched == []
    assert len(store.sync_history()) == 2


def test_dry_run_never_changes_events_ignored_cache_or_last_sync(store):
    event(store)
    before = store.total_events()
    client = IdClient([make_message("Thanks for applying to Globex", message_id="new")])
    run_sync(store, client, Analyzer(None, "off"), query="x", dry_run=True)
    assert store.total_events() == before
    assert store.get_meta("last_sync_at") is None
    assert (
        store.conn.execute("SELECT COUNT(*) FROM ignored_messages").fetchone()[0] == 0
    )


def test_recheck_can_remove_false_positive(store):
    event(store)
    client = IdClient(
        [make_message("Your receipt", body="Order confirmation", message_id="m1")]
    )
    summary = run_sync(store, client, Analyzer(None, "off"), query="x", recheck=True)
    assert summary.skipped == 1 and store.total_events() == 0
    assert store.list_applications() == []


def test_partial_failure_retains_committed_events_and_summary(store):
    class Failing:
        def iter_messages(self, *args, **kwargs):
            yield make_message("Thanks for applying to Acme", message_id="new")
            raise RuntimeError("network failed")

    with pytest.raises(RuntimeError):
        run_sync(store, Failing(), Analyzer(None, "off"), query="x")
    run = store.sync_history()[0]
    assert run["status"] == "error" and run["summary"]["scanned"] == 1
    assert store.total_events() == 1 and store.get_meta("last_sync_at") is None


def test_api_validates_corrections_and_prevents_conflicts(store):
    event(store)
    event(store, message_id="m2", thread_id="t2", role="Designer")
    client = TestClient(create_app(store.path))
    assert (
        client.patch(
            "/api/applications/1", json={"status_override": "fiction"}
        ).status_code
        == 400
    )
    assert (
        client.patch(
            "/api/applications/1", json={"follow_up_on": "invalid"}
        ).status_code
        == 400
    )
    assert client.patch("/api/applications/1", json={"company": ""}).status_code == 400
    assert (
        client.patch("/api/applications/1", json={"role": "Designer"}).status_code
        == 409
    )
    assert client.patch("/api/applications/999", json={"notes": "x"}).status_code == 404
    updated = client.patch(
        "/api/applications/1", json={"notes": "Keep this", "status_override": "offer"}
    ).json()["application"]
    assert updated["notes"] == "Keep this" and updated["status"] == "offer"


def test_overview_response_rate_counts_applications_not_messages(store):
    event(store)
    event(store, message_id="m2", kind="interview")
    event(store, message_id="m3", thread_id="t2", company="Globex")
    result = overview(store)
    assert result["total"] == 2 and result["response_rate"] == 50
    assert result["submitted"] == 2 and result["responses"] == 1
    assert len(result["activity"]) == 3


def test_historical_overview_filters_all_its_components(store):
    event(store)
    event(store, message_id="m2", kind="interview", date=BASE + timedelta(days=10))
    client = TestClient(create_app(store.path))
    result = client.get("/api/overview?as_of=2026-09-02").json()
    assert result["interviewing"] == 0 and result["response_rate"] == 0
    assert len(result["activity"]) == 1
    assert sum(w["applications"] for w in result["weeks"]) == 1
    assert result["as_of"] == "2026-09-02"


@pytest.mark.parametrize(
    "role,expected",
    [
        ("Software Engineer", "Software Development"),
        ("Yazılım Mühendisi", "Software Development"),
        (
            "Yapay Zekâ Destekli Yazılım Geliştirici (AI-Native Developer)",
            "Software Development",
        ),
        ("Backend Developer", "Backend Development"),
        ("Kıdemli Frontend Geliştirici", "Frontend Development"),
        ("Full Stack Developer", "Fullstack Development"),
        ("Mobil Uygulama Geliştirici", "Mobile Development"),
        ("DevOps Engineer", "DevOps & Cloud"),
        ("Data Engineer", "Data & Analytics"),
        ("QA Automation Engineer", "Quality Assurance"),
        ("Machine Learning Engineer", "AI & Machine Learning"),
        ("AI Engineer", "AI & Machine Learning"),
        ("Yapay Zeka Mühendisi", "AI & Machine Learning"),
        ("Product Manager", "Product & Project Management"),
        ("Barista", "Other"),
        (None, "Role not stated"),
        ("", "Role not stated"),
    ],
)
def test_role_category_normalizes_titles(role, expected):
    from jobtrack import roles

    assert roles.role_category(role) == expected


def test_overview_groups_roles_and_counts_unstated(store):
    event(store, role="Software Engineer")
    event(store, message_id="m2", thread_id="t2", role="Yazılım Mühendisi")
    event(store, message_id="m3", thread_id="t3", role="Machine Learning Engineer")
    event(store, message_id="m4", thread_id="t4", role=None)
    categories = {c["name"]: c["count"] for c in overview(store)["role_categories"]}
    assert categories["Software Development"] == 2
    assert categories["AI & Machine Learning"] == 1
    assert categories["Role not stated"] == 1
    assert sum(categories.values()) == 4


class FakeRoleLLM:
    """Minimal stand-in for an LLM client that returns a fixed mapping."""

    def __init__(self, reply, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def complete(self, system, user):
        import json

        self.calls.append((system, user))
        if self.error:
            raise self.error
        return json.dumps(self.reply)


def test_role_categorizer_asks_model_once_and_caches(tmp_path):
    from jobtrack import roles

    llm = FakeRoleLLM(
        {
            "Kıdemli Veri Mühendisi": "Data & Analytics",
            "Rockstar Ninja": "Software Development",
        }
    )
    cache_path = tmp_path / "roles.json"
    first = roles.RoleCategorizer(llm, roles.RoleCategoryCache(cache_path))
    mapping = first.map(["Kıdemli Veri Mühendisi", "Rockstar Ninja"])
    assert mapping["Kıdemli Veri Mühendisi"] == "Data & Analytics"
    assert mapping["Rockstar Ninja"] == "Software Development"
    assert len(llm.calls) == 1

    again = roles.RoleCategorizer(llm, roles.RoleCategoryCache(cache_path))
    assert again.map(["Rockstar Ninja"])["Rockstar Ninja"] == "Software Development"
    assert len(llm.calls) == 1


def test_role_categorizer_falls_back_to_rules_on_failure(tmp_path):
    from jobtrack import roles
    from jobtrack.llm import LLMError

    llm = FakeRoleLLM({}, error=LLMError("provider down"))
    categorizer = roles.RoleCategorizer(
        llm, roles.RoleCategoryCache(tmp_path / "roles.json")
    )
    assert (
        categorizer.map(["Backend Developer"])["Backend Developer"]
        == "Backend Development"
    )


def test_role_categorizer_rejects_unknown_labels(tmp_path):
    from jobtrack import roles

    llm = FakeRoleLLM({"Backend Developer": "Wizardry"})
    categorizer = roles.RoleCategorizer(
        llm, roles.RoleCategoryCache(tmp_path / "roles.json")
    )
    assert (
        categorizer.map(["Backend Developer"])["Backend Developer"]
        == "Backend Development"
    )


def test_role_categorizer_caches_titles_the_model_skips(tmp_path):
    from jobtrack import roles

    llm = FakeRoleLLM({"Known Role": "Software Development"})
    cache_path = tmp_path / "roles.json"
    categorizer = roles.RoleCategorizer(llm, roles.RoleCategoryCache(cache_path))
    mapping = categorizer.map(["Known Role", "Head of Coffee"])
    assert mapping["Known Role"] == "Software Development"
    assert mapping["Head of Coffee"] == "Other"
    assert len(llm.calls) == 1

    fresh = roles.RoleCategorizer(llm, roles.RoleCategoryCache(cache_path))
    assert fresh.map(["Head of Coffee"])["Head of Coffee"] == "Other"
    assert len(llm.calls) == 1


def test_role_categorizer_batches_large_title_sets(tmp_path):
    from jobtrack import roles

    llm = FakeRoleLLM({})
    cache_path = tmp_path / "roles.json"
    categorizer = roles.RoleCategorizer(llm, roles.RoleCategoryCache(cache_path))
    titles = [f"Role {i}" for i in range(45)]
    assert len(categorizer.map(titles)) == 45
    assert len(llm.calls) == 3  # 20 + 20 + 5, never one oversized request

    fresh = roles.RoleCategorizer(llm, roles.RoleCategoryCache(cache_path))
    fresh.map(titles)
    assert len(llm.calls) == 3


def test_build_categorizer_is_rules_only_when_mode_off():
    from jobtrack import roles, settings

    settings.save(settings.Settings(llm_mode="off"))
    assert roles.build_categorizer().llm is None


def test_overview_endpoint_uses_injected_categorizer(store):
    event(store, role="Kıdemli Veri Mühendisi")

    class FakeCategorizer:
        def map(self, roles):
            return {role: "Data & Analytics" for role in roles if role}

    client = TestClient(create_app(store.path, role_categorizer=FakeCategorizer()))
    categories = {
        category["name"]: category["count"]
        for category in client.get("/api/overview").json()["role_categories"]
    }
    assert categories == {"Data & Analytics": 1}


def test_api_assets_activity_and_security(store):
    event(store)
    client = TestClient(create_app(store.path))
    for asset in (
        "app.js",
        "api.js",
        "ui.js",
        "settings.js",
        "styles.css",
        "favicon.svg",
    ):
        assert client.get("/assets/" + asset).status_code == 200
    assert len(client.get("/api/activity").json()["events"]) == 1
    assert client.get("/api/activity?limit=999").status_code == 422
    assert client.get("/api/health").json()["database_version"] == 3
    assert (
        client.patch(
            "/api/applications/1",
            json={"notes": "x"},
            headers={"Origin": "https://example.com"},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/applications/1",
            json={"notes": "x"},
            headers={"Origin": "http://testserver"},
        ).status_code
        == 200
    )
    assert client.get("/api/applications").headers["cache-control"] == "no-store"


def test_active_filter_intersects_status_filter(store):
    event(store)
    event(store, message_id="m2", thread_id="t2", company="Globex", kind="rejected")
    client = TestClient(create_app(store.path))
    assert (
        client.get("/api/applications?active=true&status=rejected").json()[
            "applications"
        ]
        == []
    )
    assert client.get("/api/applications?status=fiction").status_code == 400


def test_settings_input_failure_leaves_previous_config_untouched(store):
    client = TestClient(create_app(store.path))
    client.put("/api/settings", json={"sync_since": "90"})
    assert (
        client.put("/api/settings", json={"sync_since": "last week"}).status_code == 400
    )
    assert client.get("/api/settings").json()["sync_since"] == "90"
    assert (
        client.put(
            "/api/settings", json={"llm_base_url": "javascript:alert(1)"}
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/settings", json={"llm_base_url": "https://user:secret@example.com"}
        ).status_code
        == 400
    )


def test_saved_mode_beats_environment(monkeypatch):
    from jobtrack import settings

    settings.save(settings.Settings(llm_mode="off"))
    monkeypatch.setenv("JOBTRACK_LLM_MODE", "always")
    assert build_analyzer().mode == "off"


def test_sync_rejects_bad_dates_before_starting_a_job(store):
    client = TestClient(create_app(store.path))
    assert client.post("/api/sync", json={"since": "yesterday"}).status_code == 400
    assert store.sync_history() == []


@pytest.mark.parametrize(
    "sender,subject,expected",
    [
        ("Upwork <notify@upwork.com>", "Proposal submitted", "freelance"),
        (
            "Client <noreply@notifications.fiverr.com>",
            "Application received",
            "freelance",
        ),
        ("Recruiter <hr@acme.com>", "Freelance designer application", "freelance"),
        ("Recruiter <hr@acme.com>", "Contract engineer application", "employment"),
        ("Recruiter <hr@upwork.com.evil.test>", "Application received", "employment"),
    ],
)
def test_category_evidence(sender, subject, expected):
    from jobtrack.categories import infer_category

    assert infer_category(sender, subject) == expected


def freelance_event(store, message_id="f1", thread_id="ft1"):
    return store.record(
        make_message(
            "Proposal submitted",
            sender="Upwork <notify@upwork.com>",
            message_id=message_id,
            thread_id=thread_id,
            date=BASE,
        ),
        Classification(kind="applied", score=12, company="Acme", role="Engineer"),
    )


def test_categories_never_merge_same_company_and_role(store):
    _, employed = event(store)
    _, freelance = freelance_event(store)
    assert employed != freelance
    assert store.get_application(freelance).category == "freelance"
    assert len(store.list_applications(category="employment")) == 1
    assert (
        len(
            store.list_applications(
                category="freelance", as_of=BASE + timedelta(days=1)
            )
        )
        == 1
    )
    assert overview(store, category="employment")["total"] == 1
    result = overview(store, category="freelance")
    assert result["total"] == result["submitted"] == 1
    assert {e["application_id"] for e in result["activity"]} == {freelance}


def test_category_correction_survives_recheck_and_conflicts_are_atomic(store):
    _, employed = event(store)
    _, freelance = freelance_event(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.update_application(
            freelance, {"category": "employment", "notes": "no save"}
        )
    assert store.get_application(freelance).notes == ""
    store.update_application(
        freelance,
        {"company": "Another client", "category": "employment", "notes": "mine"},
    )
    freelance_event(store, message_id="f2")
    assert store.get_application(freelance).category == "employment"
    assert store.get_application(freelance).notes == "mine"
    assert store.get_application(freelance).event_count == 2
    with pytest.raises(ValueError):
        store.update_application(employed, {"category": "fiction"})


def test_category_api_filters_metrics_activity_and_exports(store):
    event(store)
    _, freelance = freelance_event(store)
    client = TestClient(create_app(store.path))
    for category in ("employment", "freelance"):
        applications = client.get(
            "/api/applications", params={"category": category}
        ).json()["applications"]
        assert len(applications) == 1
        assert applications[0]["category"] == category
        assert (
            client.get("/api/overview", params={"category": category}).json()["total"]
            == 1
        )
        assert (
            len(
                client.get("/api/activity", params={"category": category}).json()[
                    "events"
                ]
            )
            == 1
        )
    assert client.get("/api/applications?category=invalid").status_code == 422
    assert (
        client.patch(
            f"/api/applications/{freelance}", json={"category": "invalid"}
        ).status_code
        == 400
    )


def test_category_migration_preserves_ids_notes_and_events(tmp_path):
    path = tmp_path / "old-v1.db"
    with Store(path) as db:
        _, app_id = freelance_event(db)
        db.update_application(app_id, {"notes": "Remember this client"})
        db.conn.execute("DROP INDEX idx_applications_category")
        db.conn.execute("UPDATE applications SET company_key = 'acme'")
        db.conn.execute("ALTER TABLE applications DROP COLUMN category")
        db.conn.execute("ALTER TABLE events DROP COLUMN review")
        db.conn.execute("DROP TABLE message_reviews")
        db.conn.execute("PRAGMA user_version=1")
        db.conn.commit()
    for _ in range(2):
        with Store(path) as db:
            assert db.get_application(app_id).category == "freelance"
            assert db.get_application(app_id).notes == "Remember this client"
            assert db.get_events(app_id)[0].message_id == "f1"
            assert db.total_events() == 1


def test_csv_export_respects_category_selection_history_and_formula_safety(store):
    _, employed = event(store)
    _, freelance = freelance_event(store)
    store.update_application(freelance, {"notes": '=HYPERLINK("evil")'})
    client = TestClient(create_app(store.path))
    response = client.get(f"/api/export?category=freelance&ids={freelance},{employed}")
    assert response.status_code == 200
    assert "jobtrack-freelance.csv" in response.headers["content-disposition"]
    import csv
    import io

    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(rows) == 2
    assert rows[1][0] == "freelance"
    assert rows[1][-1].startswith("'=HYPERLINK")
    assert (
        len(client.get("/api/export?category=employment&ids=").text.splitlines()) == 1
    )
    assert client.get("/api/export?category=employment&ids=bad").status_code == 400
    assert (
        len(
            client.get(
                "/api/export?category=freelance&as_of=2020-01-01"
            ).text.splitlines()
        )
        == 1
    )


def test_cli_category_views_and_stats(store, capsys):
    import json

    from jobtrack import cli

    event(store)
    freelance_event(store)
    assert (
        cli.main(["--db", str(store.path), "list", "--category", "freelance", "--json"])
        == 0
    )
    assert [a["category"] for a in json.loads(capsys.readouterr().out)] == ["freelance"]
    assert (
        cli.main(
            ["--db", str(store.path), "stats", "--category", "freelance", "--json"]
        )
        == 0
    )
    stats = json.loads(capsys.readouterr().out)
    assert stats["events"] == 1
    assert stats["statuses"] == {"applied": 1}
    client = TestClient(create_app(store.path))
    assert client.get("/api/stats?category=employment").json()["events"] == 1


def test_normal_sync_skips_ignored_mail_after_analyzer_version_change(store):
    store.ignore_message("old-ignored", "previous-model-and-rules")
    client = IdClient([make_message("Your receipt", message_id="old-ignored")])
    analyzer = Analyzer(None, "off")
    summary = run_sync(store, client, analyzer, query="x")
    assert client.fetched == []
    assert summary.scanned == 0 and summary.duplicate == 1


def test_normal_sync_skips_existing_unresolved_review(store):
    message = make_message("Hello", message_id="unresolved")
    store.save_review(message, {"status": "unresolved", "rounds": []})
    client = IdClient([message])
    summary = run_sync(store, client, Analyzer(None, "off"), query="x")
    assert client.fetched == []
    assert summary.scanned == 0 and summary.duplicate == 1


def test_new_email_limit_is_not_consumed_by_already_synced_ids(store):
    event(store, message_id="old-event")
    store.ignore_message("old-ignored", "old-parser")
    client = IdClient(
        [
            make_message("Application update", message_id="old-event"),
            make_message("Your receipt", message_id="old-ignored"),
            make_message("Thanks for applying to NewCo", message_id="new-one"),
            make_message("Thanks for applying to OtherCo", message_id="new-two"),
        ]
    )
    summary = run_sync(store, client, Analyzer(None, "off"), query="x", limit=1)
    assert client.fetched == ["new-one"]
    assert summary.scanned == 1 and summary.duplicate == 2
    assert store.has_message("new-one") and not store.has_message("new-two")


def test_full_sync_downloads_and_rechecks_ignored_and_recorded_mail(store):
    event(store, message_id="old-event")
    store.ignore_message("old-ignored", "old-parser")
    client = IdClient(
        [
            make_message("Thanks for applying to Acme", message_id="old-event"),
            make_message("Thanks for applying to OtherCo", message_id="old-ignored"),
        ]
    )
    summary = run_sync(store, client, Analyzer(None, "off"), query="x", recheck=True)
    assert client.fetched == ["old-event", "old-ignored"]
    assert summary.scanned == 2 and summary.duplicate == 0
    assert store.has_message("old-ignored")
