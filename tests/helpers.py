from datetime import datetime, timezone

from jobtrack.models import EmailMessage


def make_message(
    subject: str,
    body: str = "",
    sender: str = "Recruiter <recruiter@example.com>",
    message_id: str = "m1",
    thread_id: str = "t1",
    date: datetime | None = None,
    snippet: str = "",
    to: str = "",
) -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender=sender,
        to=to,
        date=date or datetime(2025, 1, 1, tzinfo=timezone.utc),
        snippet=snippet or body[:120],
        body=body,
    )
