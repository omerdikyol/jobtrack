import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jobtrack import settings
from jobtrack.analyze import Analyzer, build_analyzer
from jobtrack.classify import classify
from jobtrack.council import CouncilLLM
from jobtrack.gmail_client import default_query, parse_message
from jobtrack.llm import LLMConfig, LLMError
from jobtrack.models import Classification
from jobtrack.store import Store
from jobtrack.sync import run_sync
from jobtrack.web import create_app
from tests.helpers import make_message

BASE = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
CONFIGS = [LLMConfig(model="first"), LLMConfig(model="second")]


def reply(event="applied", company="Acme", role="Backend Engineer", evidence=""):
    return json.dumps(
        dict(
            job_related=event is not None,
            event=event,
            company=company,
            role=role,
            confidence=0.8,
            evidence=evidence,
        )
    )


def council(replies, **kwargs):
    class Fake:
        def __init__(self, config):
            self.config = config

        def complete(self, system, user):
            item = replies[self.config.model].pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    return CouncilLLM(CONFIGS, factory=Fake, **kwargs)


def test_initial_reviews_are_parallel_and_independent():
    barrier = threading.Barrier(2)
    prompts = []

    class Fake:
        def __init__(self, config):
            pass

        def complete(self, system, user):
            barrier.wait(timeout=2)
            prompts.append(user)
            return reply(evidence="We received your application")

    team = CouncilLLM(CONFIGS, factory=Fake)
    result = team.review(make_message("Application", "We received your application"))
    assert result.kind == "applied"
    assert team.last_review["status"] == "agreed"
    assert len(team.last_review["rounds"]) == 1
    assert all("PEER VERDICTS" not in p for p in prompts)
    assert (
        result.review["rounds"][0]["opinions"][0]["evidence"]
        == "We received your application"
    )


def test_discussion_compares_verdicts_then_reaches_agreement():
    prompts = []
    replies = {"first": [reply(), reply()], "second": [reply("interview"), reply()]}

    class Fake:
        def __init__(self, config):
            self.config = config

        def complete(self, system, user):
            prompts.append(user)
            return replies[self.config.model].pop(0)

    team = CouncilLLM(CONFIGS, factory=Fake)
    result = team.review(
        make_message("Application"), "2026-10-01T09:00:00+00:00 | LinkedIn confirmation"
    )
    assert result.kind == "applied"
    assert len(team.last_review["rounds"]) == 2
    assert team.last_review["context_used"]
    assert len([p for p in prompts if "PEER VERDICTS" in p]) == 2
    assert "interview" in prompts[-1]


def test_enduring_disagreement_is_bounded_and_preserves_rules():
    team = council(
        {"first": [reply()] * 3, "second": [reply("interview")] * 3}, rounds=2
    )
    analyzer = Analyzer(team, "always")
    result, source = analyzer.analyze(
        make_message(
            "Thank you for applying to Acme",
            "We received your application for the Backend Engineer role.",
        )
    )
    assert source == "rules"
    assert result.kind == "applied"
    assert result.review["status"] == "unresolved"
    assert len(result.review["rounds"]) == 3
    assert analyzer.disabled_reason is None


@pytest.mark.parametrize(
    "bad", [LLMError("api-key-secret"), "{}", '{"job_related":true,"event":"invented"}']
)
def test_failure_is_not_a_vote_and_provider_details_are_sanitized(bad):
    team = council({"first": [reply()], "second": [bad]})
    assert team.review(make_message("Application")) is None
    report = json.dumps(team.last_review)
    assert team.last_review["status"] == "unresolved"
    assert len(team.last_review["rounds"]) == 1
    assert "api-key-secret" not in report


def test_timeout_returns_without_waiting_for_slow_reviewer():
    class Slow:
        def __init__(self, config):
            pass

        def complete(self, system, user):
            time.sleep(0.1)
            return reply()

    team = CouncilLLM(CONFIGS, factory=Slow, round_timeout=0.015)
    start = time.monotonic()
    assert team.review(make_message("Application")) is None
    assert time.monotonic() - start < 0.09
    assert team.last_review["status"] == "unresolved"
    assert "time limit" in team.last_review["rounds"][0]["opinions"][0]["error"]


def test_identity_disagreement_is_not_reported_as_consensus():
    team = council(
        {"first": [reply(role="Engineer")] * 2, "second": [reply(role="Designer")] * 2},
        rounds=1,
    )
    assert team.review(make_message("Application")) is None
    assert team.last_review["status"] == "unresolved"


def test_equivalent_company_punctuation_and_role_case_agree():
    team = council(
        {
            "first": [reply(company="Acme, Inc.")],
            "second": [reply(company="acme", role="backend engineer")],
        }
    )
    assert team.review(make_message("Application")).kind == "applied"


def test_unrelated_consensus_is_valid_and_fabricated_evidence_is_dropped():
    team = council(
        {"first": [reply(None, evidence="made up quote")], "second": [reply(None)]}
    )
    assert team.review(make_message("Newsletter")) is None
    assert team.last_review["status"] == "agreed"
    assert team.last_review["rounds"][0]["opinions"][0]["evidence"] == ""


def test_cache_changes_with_team_endpoint_and_round_budget():
    first = Analyzer(CouncilLLM(CONFIGS), "always").cache_key
    assert first != Analyzer(CouncilLLM(CONFIGS, rounds=1), "always").cache_key
    changed = [CONFIGS[0], LLMConfig(model="second", base_url="http://127.0.0.1:9999")]
    assert first != Analyzer(CouncilLLM(changed), "always").cache_key


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "api.db"))


def test_multiple_provider_connections_preserve_and_mask_secrets(client):
    response = client.put(
        "/api/settings",
        json={
            "connections": {
                "groq": {"api_key": "first-private-secret-key"},
                "openai": {
                    "api_key": "second-private-secret-key",
                    "base_url": "http://localhost:9001/v1",
                },
            }
        },
    )
    assert response.status_code == 200
    assert "first-private-secret-key" not in response.text
    assert "second-private-secret-key" not in response.text
    assert response.json()["connections"]["openai"]["is_local"]
    client.put(
        "/api/settings",
        json={"connections": {"groq": {"base_url": "https://api.groq.com/openai/v1"}}},
    )
    assert settings.load().llm_api_keys["groq"] == "first-private-secret-key"
    client.put("/api/settings", json={"connections": {"groq": {"api_key": ""}}})
    assert "groq" not in settings.load().llm_api_keys
    assert "openai" in settings.load().llm_api_keys


@pytest.mark.parametrize(
    "payload",
    [
        {
            "review_strategy": "consensus",
            "review_models": [{"provider": "ollama", "model": "first"}],
        },
        {"review_models": [{"provider": "ollama", "model": "first"}] * 2},
        {"review_models": [{"provider": "nope", "model": "first"}]},
        {"review_models": [{"provider": "ollama", "model": "   "}]},
        {"review_rounds": 4},
        {"review_strategy": "majority"},
        {"connections": {"groq": {"base_url": "https://user:secret@example.com"}}},
    ],
)
def test_invalid_team_update_is_atomic(client, payload):
    client.put("/api/settings", json={"connections": {"groq": {"api_key": "keep-me"}}})
    before = settings.SETTINGS_PATH.read_bytes()
    response = client.put("/api/settings", json=payload)
    assert response.status_code in (400, 422)
    assert settings.SETTINGS_PATH.read_bytes() == before


def test_configured_team_is_used_by_sync_and_sample_test(client, monkeypatch):
    from jobtrack import web

    response = client.put(
        "/api/settings",
        json={
            "review_strategy": "consensus",
            "review_models": [
                {"provider": "ollama", "model": m} for m in ("first", "second")
            ],
            "llm_mode": "always",
        },
    )
    assert response.status_code == 200
    assert isinstance(build_analyzer().llm, CouncilLLM)

    class Fake:
        def __init__(self, config):
            pass

        def complete(self, system, user):
            return reply("rejected", "Initech", None)

    monkeypatch.setattr(web, "build_llm", Fake)
    tested = client.post("/api/settings/test").json()
    assert tested["ok"] and tested["sample"]["event"] == "rejected"
    assert tested["review"]["status"] == "agreed"


class Mailbox:
    def __init__(self, messages):
        self.messages = messages

    def iter_messages(self, query, limit=0):
        yield from self.messages


def test_linkedin_and_company_confirmations_merge_in_received_order(tmp_path):
    earlier = make_message(
        "Your application was sent to Acme",
        "Backend Engineer\nYour application was sent to Acme.",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
        message_id="linkedin",
        thread_id="linkedin-thread",
        date=BASE,
    )
    later = make_message(
        "Thanks for applying to Acme",
        "We received your application for the Backend Engineer role.",
        sender="Acme Hiring <jobs@acme.com>",
        message_id="company",
        thread_id="company-thread",
        date=BASE + timedelta(seconds=32),
    )
    contexts = []

    class ContextAnalyzer(Analyzer):
        def set_context(self, context):
            contexts.append(context)
            super().set_context(context)

    # Use a fake single-model client to exercise the contextual model path.
    class Fake:
        provider = "ollama"
        model = "first"
        max_chars = 4000

        def complete(self, system, user):
            return reply()

    analyzer = ContextAnalyzer(Fake(), "always")
    with Store(tmp_path / "chronology.db") as store:
        summary = run_sync(store, Mailbox([later, earlier]), analyzer, query="x")
        assert summary.created == 1 and summary.matched == 1
        apps = store.list_applications()
        assert len(apps) == 1 and apps[0].role == "Backend Engineer"
        events = store.get_events(apps[0].id)
        assert [ev.message_id for ev in events] == ["linkedin", "company"]
        assert (events[1].event_date - events[0].event_date).total_seconds() == 32
        assert "LinkedIn" in contexts[1] and "09:00:00" in contexts[1]


def test_close_arrivals_and_same_thread_do_not_merge_distinct_roles(tmp_path):
    with Store(tmp_path / "roles.db") as store:
        first = make_message(
            "Application", thread_id="shared", message_id="one", date=BASE
        )
        second = make_message(
            "Application",
            thread_id="shared",
            message_id="two",
            date=BASE + timedelta(seconds=1),
        )
        store.record(first, Classification("applied", 12, "Acme", "Engineer"))
        assert (
            store.related_context(
                second, Classification("applied", 12, "Acme", "Designer")
            )
            == ""
        )
        store.record(second, Classification("applied", 12, "Acme", "Designer"))
        assert len(store.list_applications()) == 2


def test_context_never_contains_future_updates(tmp_path):
    with Store(tmp_path / "future.db") as store:
        store.record(
            make_message(
                "Rejected", message_id="future", date=BASE + timedelta(days=1)
            ),
            Classification("rejected", 12, "Acme", "Engineer"),
        )
        current = make_message("Applied", message_id="current", date=BASE)
        assert (
            store.related_context(
                current, Classification("applied", 12, "Acme", "Engineer")
            )
            == ""
        )


def test_unresolved_review_is_persisted_without_caching_or_deleting_mail(tmp_path):
    def analyzer():
        return Analyzer(
            council(
                {"first": [reply()] * 2, "second": [reply("interview")] * 2}, rounds=1
            ),
            "always",
        )

    message = make_message("Ordinary greeting", "Hello.", date=BASE)
    with Store(tmp_path / "reviews.db") as store:
        summary = run_sync(store, Mailbox([message]), analyzer(), query="x")
        assert summary.unresolved == 1
        assert len(store.recent_reviews("unresolved")) == 1
        assert message.message_id not in store.known_message_ids(analyzer().cache_key)
        store.record(message, Classification("interview", 12, "Acme", "Engineer"))
        run_sync(store, Mailbox([message]), analyzer(), query="x", recheck=True)
        assert store.total_events() == 1 and store.get_events(1)[0].kind == "interview"


def test_dry_run_leaves_review_history_untouched(tmp_path):
    team = council({"first": [reply()], "second": [reply()]})
    with Store(tmp_path / "dry.db") as store:
        run_sync(
            store,
            Mailbox([make_message("Application")]),
            Analyzer(team, "always"),
            query="x",
            dry_run=True,
        )
        assert store.recent_reviews() == [] and store.total_events() == 0


def test_gmail_receipt_time_wins_over_sender_clock_and_query_includes_linkedin():
    message = parse_message(
        {
            "id": "1",
            "internalDate": str(int(BASE.timestamp() * 1000) + 32123),
            "payload": {
                "headers": [{"name": "Date", "value": "Wed, 1 Jan 2025 10:00:00 +0000"}]
            },
        }
    )
    assert message.date == BASE + timedelta(seconds=32, milliseconds=123)
    assert (
        '"you applied"' in default_query()
        and '"application was sent"' in default_query()
    )


def test_cli_provider_override_uses_that_providers_key():
    from jobtrack import cli

    settings.save(
        settings.Settings(
            llm_provider="groq",
            llm_api_keys={"groq": "groq-secret", "openai": "openai-secret"},
        )
    )
    config = cli._llm_config(
        cli.build_parser().parse_args(["sync", "--llm-provider", "openai"])
    )
    assert config.provider == "openai" and config.api_key == "openai-secret"


def test_legacy_single_selection_replaces_saved_team(client):
    client.put(
        "/api/settings",
        json={
            "review_models": [
                {"provider": "ollama", "model": m} for m in ("first", "second")
            ],
            "review_strategy": "consensus",
        },
    )
    response = client.put(
        "/api/settings", json={"llm_provider": "groq", "llm_model": "new-primary"}
    )
    assert response.status_code == 200
    assert response.json()["review_strategy"] == "single"
    assert response.json()["review_models"] == [
        {"provider": "groq", "model": "new-primary"}
    ]


def test_linkedin_standalone_title_is_extracted_by_rules():
    from jobtrack.classify import classify

    result = classify(
        make_message(
            "Your application was sent to Acme",
            "Backend Engineer\nYour application was sent to Acme.",
            sender="LinkedIn <jobs-noreply@linkedin.com>",
        )
    )
    assert (result.kind, result.company, result.role) == (
        "applied",
        "Acme",
        "Backend Engineer",
    )


@pytest.mark.parametrize(
    "company,role",
    [
        ("BeInsights", "Java Yazılım Geliştirme Uzmanı"),
        ("Mento HR", "Yazılım Mühendisi"),
        (
            "Lorka | All-in-One AI Access",
            "Join the Lorka Journey: Future Opportunities & Talent Community",
        ),
    ],
)
def test_turkish_linkedin_confirmation_uses_applied_card_not_recommended_jobs(
    company, role
):
    from jobtrack.classify import classify

    message = make_message(
        f"Sam, başvurunuz {company} şirketine gönderildi",
        f"Başvurunuz {company} şirketine gönderildi\n{role}\n{company}\nTürkiye\nİlgilenebileceğiniz benzer iş ilanları\nBackend Developer\nSoftware Engineer\nBu e-posta, Sam (Software Engineer & Researcher) için gönderilmiştir",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )
    result = classify(message)
    assert (result.kind, result.company, result.role) == ("applied", company, role)
    assert '"başvurunuz"' in default_query()


def test_linkedin_html_application_card_survives_empty_plain_notification():
    import base64

    from jobtrack.classify import extract_role

    def encode(text):
        return base64.urlsafe_b64encode(text.encode()).decode()

    raw = {
        "id": "card",
        "internalDate": str(int(BASE.timestamp() * 1000)),
        "payload": {
            "headers": [
                {"name": "From", "value": "LinkedIn <jobs-noreply@linkedin.com>"},
                {
                    "name": "Subject",
                    "value": "Acme şirketindeki Backend Developer başvurunuz",
                },
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": encode("Application updates from Acme. Unsubscribe.")
                    },
                },
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": encode(
                            "<h2>Başvurunuz görüntülendi</h2><h3>Backend Developer</h3><div>Acme</div>"
                        )
                    },
                },
            ],
        },
    }
    message = parse_message(raw)
    assert "Başvurunuz görüntülendi" in message.body
    assert extract_role(message) == "Backend Developer"


def test_contradictory_relevance_does_not_count_as_agreement():
    invalid = json.dumps({"job_related": False, "event": "rejected", "company": "Acme"})
    team = council({"first": [invalid], "second": [invalid]})
    assert team.review(make_message("Application")) is None
    assert team.last_review["status"] == "unresolved"


def test_turkish_rejection_and_view_update_keep_the_applied_title():
    from jobtrack.classify import classify

    rejection = make_message(
        "Acme şirketindeki Yazılım Mühendisi başvurunuz",
        "Ne yazık ki, başvurunuzla devam etmeyeceğiz.",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )
    result = classify(rejection)
    assert (result.kind, result.company, result.role) == (
        "rejected",
        "Acme",
        "Yazılım Mühendisi",
    )
    viewed = make_message(
        "Başvurunuz Acme tarafından görüntülendi",
        "Başvurunuz görüntülendi\nAcme şirketindeki işe alım takımı tarafından fark edildiniz\nYazılım Mühendisi\nAcme · Türkiye\nSizin için önerilen benzer iş ilanları\nBackend Developer",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )
    result = classify(viewed)
    assert (result.kind, result.company, result.role) == (
        "applied",
        "Acme",
        "Yazılım Mühendisi",
    )


def test_invisible_padding_is_removed_without_dropping_joiners_or_accents():
    from jobtrack.llm import render_email

    message = make_message(
        "Application", "\u034f " * 5000 + "Yazılım Mühendisi 👩\u200d💻"
    )
    assert "\u034f" not in message.body and "\u200d" in message.body
    assert "Yazılım Mühendisi" in render_email(message)


def test_turkish_company_case_variants_match_without_rewriting_old_keys(tmp_path):
    with Store(tmp_path / "unicode.db") as db:
        _, app_id = db.record(
            make_message("Application", message_id="one", thread_id="one"),
            Classification(
                "applied",
                12,
                "GELECEK VARLIK YÖNETİMİ A.Ş.",
                "Yazılım Geliştirme Uzmanı",
            ),
        )
        original_key = db.conn.execute(
            "SELECT company_key FROM applications WHERE id=?", (app_id,)
        ).fetchone()[0]
        outcome, matched_id = db.record(
            make_message("Application update", message_id="two", thread_id="two"),
            Classification(
                "applied",
                12,
                "Gelecek Varlık Yönetimi A.Ş.",
                "Yazılım Geliştirme Uzmanı",
            ),
        )
        assert outcome == "matched" and matched_id == app_id
        assert (
            db.conn.execute(
                "SELECT company_key FROM applications WHERE id=?", (app_id,)
            ).fetchone()[0]
            == original_key
        )


def test_roleless_recheck_retains_known_identity_in_ambiguous_company(tmp_path):
    with Store(tmp_path / "recheck.db") as db:
        original = make_message("Application", message_id="one", thread_id="")
        _, app_id = db.record(
            original, Classification("applied", 12, "Acme", "Engineer")
        )
        db.record(
            make_message("Application", message_id="two", thread_id=""),
            Classification("applied", 12, "Acme", "Designer"),
        )
        db.update_application(app_id, {"notes": "Keep this application"})
        _, rechecked = db.record(
            original, Classification("applied", 12, "Acme"), replace=True
        )
        assert rechecked == app_id and len(db.list_applications()) == 2
        assert db.get_application(app_id).notes == "Keep this application"


def test_linkedin_repeated_heading_with_ai_employer_is_not_role():
    company = "Lorka | All-in-One AI Access"
    role = "Join the Lorka Journey: Future Opportunities & Talent Community"
    heading = f"Başvurunuz {company} şirketine gönderildi"
    msg = make_message(
        f"Sam, {heading}",
        f"{heading}\n{heading}\n{role}\n{company} · Avrupa (Uzaktan)\nİlgilenebileceğiniz benzer iş ilanları\nBackend Developer",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )
    result = classify(msg)
    assert result.company == company
    assert result.role == role


def test_unanimity_cannot_move_linkedin_mail_to_different_employer():
    msg = make_message(
        "Taki Regnskap şirketindeki Fullstack Developer başvurunuz",
        "Fullstack Developer\nTaki Regnskap · Oslo\nThank you for your interest at Mandora. Unfortunately, we will not be moving forward with your application.",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )
    reviewer = council(
        {
            model: [reply("rejected", "Mandora", "Fullstack Developer")]
            for model in ("first", "second")
        }
    )
    result, source = Analyzer(reviewer, mode="always").analyze(msg)
    assert source == "rules"
    assert result.company == "Taki Regnskap"
    assert result.kind == "rejected"
    assert result.review["status"] == "unresolved"
    assert "application header" in result.review["resolution"]


def test_shared_thread_does_not_merge_known_different_employers(tmp_path):
    with Store(tmp_path / "same-thread.db") as store:
        first = make_message("Application", thread_id="shared", message_id="first")
        second = make_message("Rejection", thread_id="shared", message_id="second")
        _, original = store.record(
            first, Classification("applied", 12, "Taki Regnskap", "Developer")
        )
        outcome, other = store.record(
            second, Classification("rejected", 12, "Mandora", "Developer")
        )
        assert outcome == "created" and other != original
        assert store.get_application(original).status == "applied"


def test_consensus_cannot_override_upwork_invitation_job_name():
    msg = make_message(
        "Invitation to Interview for: Interview Process",
        sender="Upwork <notify@upwork.com>",
    )
    team = council(
        {
            model: [reply("interview", "Upwork", "Software Engineer")]
            for model in ("first", "second")
        }
    )
    result, source = Analyzer(team, mode="always").analyze(msg)
    assert source == "rules"
    assert result.role == "Interview Process"
    assert result.review["status"] == "unresolved"
    assert "Upwork invitation title" in result.review["resolution"]
