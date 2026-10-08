from datetime import datetime, timedelta, timezone

import pytest

from jobtrack.models import Classification
from jobtrack.store import Store, normalize_company
from tests.helpers import make_message

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def meticulous(kind: str, company: str | None, role: str | None = None, score: int = 8):
    return Classification(kind=kind, score=score, company=company, role=role, matched=[kind])


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "test.db") as s:
        yield s


def test_normalize_company_strips_legal_suffixes():
    assert normalize_company("Acme, Inc.") == "acme"
    assert normalize_company("ACME") == "acme"
    assert normalize_company("Acme GmbH") == "acme"


def test_record_creates_application(store):
    message = make_message("Thank you for applying to Acme", message_id="m1")
    outcome, app_id = store.record(message, meticulous("applied", "Acme"))

    assert outcome == "created"
    app = store.get_application(app_id)
    assert app.company == "Acme"
    assert app.status == "applied"
    assert app.event_count == 1


def test_duplicate_message_is_ignored(store):
    message = make_message("Thank you for applying to Acme", message_id="m1")
    store.record(message, meticulous("applied", "Acme"))

    outcome, app_id = store.record(message, meticulous("applied", "Acme"))
    assert outcome == "duplicate"
    assert app_id is None
    assert len(store.list_applications()) == 1
    assert store.total_events() == 1


def test_same_thread_attaches_to_existing_application(store):
    first = make_message("Thanks for applying to Acme", message_id="m1", thread_id="t1")
    _, app_id = store.record(first, meticulous("applied", "Acme"))

    # A follow-up whose company could not be parsed still lands on the thread.
    second = make_message(
        "Next steps",
        message_id="m2",
        thread_id="t1",
        date=BASE + timedelta(days=3),
    )
    outcome, second_id = store.record(second, meticulous("interview", None))

    assert outcome == "matched"
    assert second_id == app_id
    assert store.get_application(app_id).status == "interview"


def test_company_match_without_thread(store):
    first = make_message(
        "Thanks for applying to Acme", message_id="m1", thread_id="t1"
    )
    _, app_id = store.record(first, meticulous("applied", "Acme, Inc."))

    second = make_message(
        "Interview invitation", message_id="m2", thread_id="t2",
        date=BASE + timedelta(days=2),
    )
    outcome, second_id = store.record(second, meticulous("interview", "acme"))

    assert outcome == "matched"
    assert second_id == app_id


def test_status_progresses_and_rejection_wins(store):
    store.record(
        make_message("Thanks for applying", message_id="m1", thread_id="t1"),
        meticulous("applied", "Acme"),
    )
    store.record(
        make_message("Interview invite", message_id="m2", thread_id="t1"),
        meticulous("interview", "Acme"),
    )
    assert store.list_applications()[0].status == "interview"

    store.record(
        make_message("Update", message_id="m3", thread_id="t1"),
        meticulous("rejected", "Acme"),
    )
    assert store.list_applications()[0].status == "rejected"


def test_offer_outranks_rejection(store):
    store.record(
        make_message("Interview", message_id="m1", thread_id="t1"),
        meticulous("interview", "Acme"),
    )
    store.record(
        make_message("Offer", message_id="m2", thread_id="t1"),
        meticulous("offer", "Acme"),
    )
    store.record(
        make_message("Nope", message_id="m3", thread_id="t1"),
        meticulous("rejected", "Acme"),
    )
    assert store.list_applications()[0].status == "offer"


def test_timeline_is_chronological(store):
    store.record(
        make_message("Applied", message_id="m2", thread_id="t1", date=BASE + timedelta(days=5)),
        meticulous("interview", "Acme"),
    )
    store.record(
        make_message("Original", message_id="m1", thread_id="t1", date=BASE),
        meticulous("applied", "Acme"),
    )

    app = store.list_applications()[0]
    events = store.get_events(app.id)

    assert [e.message_id for e in events] == ["m1", "m2"]
    assert app.first_seen == BASE
    assert app.last_event_at == BASE + timedelta(days=5)


def test_separate_companies_create_separate_applications(store):
    store.record(
        make_message("Applied to Acme", message_id="m1", thread_id="t1"),
        meticulous("applied", "Acme"),
    )
    store.record(
        make_message("Applied to Globex", message_id="m2", thread_id="t2"),
        meticulous("applied", "Globex"),
    )
    assert len(store.list_applications()) == 2


def test_list_filters_by_status(store):
    store.record(
        make_message("Applied", message_id="m1", thread_id="t1"),
        meticulous("applied", "Acme"),
    )
    store.record(
        make_message("Rejected", message_id="m2", thread_id="t2"),
        meticulous("rejected", "Globex"),
    )

    rejected = store.list_applications(statuses=["rejected"])
    assert [a.company for a in rejected] == ["Globex"]


def test_find_application_by_name(store):
    store.record(
        make_message("Applied", message_id="m1", thread_id="t1"),
        meticulous("applied", "Northwind Traders", role="Data Analyst"),
    )
    assert store.find_application_by_name("northwind")[0].company == "Northwind Traders"
    assert store.find_application_by_name("data analyst")[0].role == "Data Analyst"


def test_status_counts_and_meta(store):
    store.record(
        make_message("Applied", message_id="m1", thread_id="t1"),
        meticulous("applied", "Acme"),
    )
    store.set_meta("last_sync_at", "2025-01-01T00:00:00+00:00")

    assert store.status_counts() == {"applied": 1}
    assert store.get_meta("last_sync_at") == "2025-01-01T00:00:00+00:00"


def test_recheck_replaces_previous_classification(store):
    message = make_message("Application update", message_id="m1", thread_id="t1")
    store.record(message, meticulous("applied", "Acme"))

    outcome, app_id = store.record(message, meticulous("rejected", "Acme"), replace=True)

    assert outcome == "matched"
    assert store.total_events() == 1
    assert store.get_application(app_id).status == "rejected"


def test_recheck_moving_company_leaves_no_orphan_application(store):
    message = make_message("Application update", message_id="m1", thread_id="t1")
    _, original_id = store.record(message, meticulous("applied", "Acme"))

    outcome, new_id = store.record(message, meticulous("applied", "Globex"), replace=True)

    assert outcome == "created"
    assert new_id != original_id
    assert [a.company for a in store.list_applications()] == ["Globex"]
    assert store.get_application(original_id) is None


def test_recheck_without_flag_still_deduplicates(store):
    message = make_message("Application update", message_id="m1", thread_id="t1")
    store.record(message, meticulous("applied", "Acme"))

    outcome, app_id = store.record(message, meticulous("rejected", "Acme"))

    assert outcome == "duplicate"
    assert store.get_application(1).status == "applied"


# --------------------------------------------------------------------------
# Replaying history
# --------------------------------------------------------------------------
@pytest.fixture
def history(tmp_path):
    """Acme: applied -> interview -> rejected. Globex: applied, later."""
    with Store(tmp_path / "history.db") as store:
        for message, classification in (
            (make_message("Applied", message_id="m1", thread_id="t1", date=BASE),
             meticulous("applied", "Acme")),
            (make_message("Interview", message_id="m2", thread_id="t1", date=BASE + timedelta(days=10)),
             meticulous("interview", "Acme")),
            (make_message("Rejected", message_id="m3", thread_id="t1", date=BASE + timedelta(days=30)),
             meticulous("rejected", "Acme")),
            (make_message("Applied to Globex", message_id="m4", thread_id="t4", date=BASE + timedelta(days=20)),
             meticulous("applied", "Globex")),
        ):
            store.record(message, classification)
        yield store


def test_as_of_before_anything_returns_nothing(history):
    assert history.list_applications(as_of=BASE - timedelta(days=1)) == []


def test_as_of_replays_the_status_at_that_moment(history):
    def statuses(days):
        at = BASE + timedelta(days=days)
        return {a.company: a.status for a in history.list_applications(as_of=at)}

    assert statuses(1) == {"Acme": "applied"}
    assert statuses(11) == {"Acme": "interview"}
    assert statuses(21) == {"Acme": "interview", "Globex": "applied"}
    assert statuses(31) == {"Acme": "rejected", "Globex": "applied"}


def test_as_of_includes_the_whole_day_it_names(history):
    # An event at 00:00 UTC on day 10 must count when asking for day 10.
    end_of_day_10 = BASE + timedelta(days=10) + timedelta(hours=23, minutes=59)
    assert [a.status for a in history.list_applications(as_of=end_of_day_10)] == ["interview"]


def test_as_of_omits_applications_that_had_not_started_yet(history):
    def at(days):
        return BASE + timedelta(days=days)

    # Globex does not apply until day 20, so it is absent on day 15...
    assert [a.company for a in history.list_applications(as_of=at(15))] == ["Acme"]
    # ...and present on day 25, ahead of Acme because its activity is newer.
    assert [a.company for a in history.list_applications(as_of=at(25))] == ["Globex", "Acme"]


def test_as_of_count_is_zero_for_a_year_before_any_activity(history):
    assert history.list_applications(as_of=BASE - timedelta(days=365)) == []


def test_as_of_counts_and_dates_reflect_only_past_events(history):
    application = history.list_applications(as_of=BASE + timedelta(days=11))[0]

    assert application.company == "Acme"
    assert application.event_count == 2
    assert application.first_seen == BASE
    assert application.last_event_at == BASE + timedelta(days=10)


def test_as_of_filters_by_replayed_status(history):
    at = BASE + timedelta(days=11)
    assert [a.status for a in history.list_applications(statuses=["interview"], as_of=at)] == [
        "interview"
    ]
    # The stored status is "rejected", so filtering on the live value would fail.
    assert history.list_applications(statuses=["rejected"], as_of=at) == []


def test_as_of_events_are_truncated(history):
    application = history.get_application(1)

    all_events = history.get_events(application.id)
    assert [e.kind for e in all_events] == ["applied", "interview", "rejected"]

    early = history.get_events(application.id, as_of=BASE + timedelta(days=11))
    assert [e.kind for e in early] == ["applied", "interview"]


def test_as_of_application_lookup_returns_the_old_status(history):
    assert history.get_application(1, as_of=BASE + timedelta(days=1)).status == "applied"
    assert history.get_application(1).status == "rejected"
    # Not yet applied for at all.
    assert history.get_application(1, as_of=BASE - timedelta(days=5)) is None


def test_as_of_counts_and_totals(history):
    at = BASE + timedelta(days=21)

    assert history.status_counts(as_of=at) == {"interview": 1, "applied": 1}
    assert history.total_events(as_of=at) == 3
    assert history.total_events() == 4
