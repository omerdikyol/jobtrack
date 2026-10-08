"""Applications the user sent themselves, recovered from their own mailbox."""

from datetime import datetime, timezone

from jobtrack.analyze import Analyzer
from jobtrack.classify import outbound_application
from jobtrack.gmail_client import sent_query
from jobtrack.models import EmailMessage
from jobtrack.store import Store
from jobtrack.sync import run_sync
from tests.helpers import make_message


def sent(subject, to, **kwargs):
    return make_message(
        subject,
        sender="Sam Rivera <me@example.com>",
        to=to,
        **kwargs,
    )


def employer_of(subject, to):
    result = outbound_application(sent(subject, to))
    assert result is not None, subject
    return result.company, result.role


def test_subject_naming_the_employer_beats_the_domain():
    assert employer_of(
        "Application for Game Developer Position at Northlight Games",
        "careers@northlightgames.co",
    ) == ("Northlight Games", "Game Developer")


def test_employer_comes_from_the_recipient_domain():
    assert employer_of(
        "Application for Junior Software Engineer", "jobs@brightline.eu"
    ) == ("Brightline", "Junior Software Engineer")


def test_multi_part_public_suffix_keeps_the_employer_label():
    # "aday@kalemci.com.tr" must not read as "Com".
    assert employer_of("Sam Rivera CV", "aday@kalemci.com.tr") == (
        "Kalemci",
        None,
    )


def test_ats_local_part_supplies_the_employer():
    assert employer_of(
        "Application for Opportunities at Novastudio",
        "novastudio@jobs.workablemail.com",
    ) == ("Novastudio", None)


def test_personal_contact_is_not_an_employer():
    assert outbound_application(
        sent("CV for Full-Stack, React, Node.js", "friend@gmail.com")
    ) is None


def test_unrecognisable_domain_is_not_an_employer():
    # "abc.ie" would read as "Abc"; better ignored than wrongly recorded.
    assert (
        outbound_application(
            sent("Research Engineer Application", "someone@abc.ie")
        )
        is None
    )


def test_mail_without_an_application_subject_is_ignored():
    assert outbound_application(
        sent("Meeting notes Thursday", "sam@example.com")
    ) is None


def test_to_header_is_parsed_with_display_names():
    message = EmailMessage(
        message_id="m",
        thread_id="t",
        subject="Application for Engineer",
        sender="me@example.com",
        to='"Doe, Jane" <jane@acme.co>, other@acme.co',
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert message.to_emails == ["jane@acme.co", "other@acme.co"]
    assert message.to_email == "jane@acme.co"
    assert message.to_name == "Doe, Jane"


def test_sent_query_searches_the_mailbox_owner():
    query = sent_query("newer_than:90d")
    assert query.startswith("from:me")
    assert "application" in query
    assert query.endswith("newer_than:90d")


def test_run_sync_records_outbound_applications_only_when_asked():
    class Client:
        """Both inbound recruiting mail and the user's own application."""

        def __init__(self) -> None:
            self.queries: list[str] = []

        def iter_message_ids(self, query, limit=0):
            if query not in self.queries:
                self.queries.append(query)
            if query.startswith("from:me"):
                yield "sent-1"
                return
            yield "in-1"

        def get_message(self, message_id):
            if message_id == "sent-1":
                return sent(
                    "Application for Junior Software Engineer",
                    "jobs@brightline.eu",
                    message_id=message_id,
                    thread_id="t-sent",
                )
            return make_message(
                "Thanks for applying to Acme",
                sender="Acme <jobs@acme.com>",
                message_id=message_id,
            )

    client = Client()
    with Store(":memory:") as store:
        summary = run_sync(
            store,
            client,
            Analyzer(None, mode="off"),
            query="subject:(application)",
            own_address="me@example.com",
            track_sent=True,
        )
        applications = {(a.company, a.role) for a in store.list_applications()}

    assert summary.sent == 1
    assert summary.created == 2
    assert ("Brightline", "Junior Software Engineer") in applications
    assert ("Acme", None) in applications
    # The sent search runs first: the inbound query can return thousands of
    # ids and would otherwise use up the message limit.
    assert client.queries[0].startswith("from:me")
    assert "subject:(application)" in client.queries[1]


def test_sent_applications_are_not_starved_by_a_long_inbound_search():
    """A capped sync must still reach the user's own applications."""

    class Client:
        def iter_message_ids(self, query, limit=0):
            if query.startswith("from:me"):
                yield "sent-1"
                return
            yield from (f"in-{i}" for i in range(50))

        def get_message(self, message_id):
            if message_id == "sent-1":
                return sent(
                    "Application for Junior Software Engineer",
                    "jobs@brightline.eu",
                    message_id=message_id,
                    thread_id="t-sent",
                )
            return make_message(
                "Thanks for applying to Acme",
                sender="Acme <jobs@acme.com>",
                message_id=message_id,
            )

    with Store(":memory:") as store:
        summary = run_sync(
            store,
            Client(),
            Analyzer(None, mode="off"),
            query="subject:(application)",
            limit=10,
            own_address="me@example.com",
            track_sent=True,
        )
        companies = {a.company for a in store.list_applications()}

    assert summary.sent == 1
    assert "Brightline" in companies


def test_outbound_application_does_not_trip_the_review_guard():
    """A recorded outbound application is not an unresolved consensus review.

    Regression: `unresolved` was only bound inside the analyzer branch, so the
    first recorded application of a run raised UnboundLocalError.
    """

    class Client:
        def iter_message_ids(self, query, limit=0):
            yield "sent-1"

        def get_message(self, message_id):
            # The one and only message is the user's own application, so it is
            # the first thing the loop sees: no analyzer branch runs before it.
            return sent(
                "Application for Junior Software Engineer",
                "jobs@brightline.eu",
                message_id=message_id,
                thread_id="t-sent",
            )

    with Store(":memory:") as store:
        summary = run_sync(
            store,
            Client(),
            Analyzer(None, mode="off"),
            query="subject:(application)",
            own_address="me@example.com",
            track_sent=True,
        )
        companies = {a.company for a in store.list_applications()}

    assert summary.sent == 1
    assert summary.created == 1
    assert companies == {"Brightline"}


def test_run_sync_ignores_outbound_applications_by_default():
    class Client:
        def iter_message_ids(self, query, limit=0):
            if query.startswith("from:me"):
                return
            yield "sent-1"

        def get_message(self, message_id):
            return sent(
                "Application for Junior Software Engineer",
                "jobs@brightline.eu",
                message_id=message_id,
                thread_id="t-sent",
            )

    with Store(":memory:") as store:
        summary = run_sync(
            store,
            Client(),
            Analyzer(None, mode="off"),
            query="subject:(application)",
            own_address="me@example.com",
        )
        assert summary.sent == 0
        assert summary.skipped == 1
        assert store.list_applications() == []
        assert store.ignored_message_ids()  # cached as ignored, as before
        assert "sent-1" in store.ignored_message_ids()
