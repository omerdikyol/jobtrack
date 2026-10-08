import base64

from jobtrack.gmail_client import default_query, parse_message


def encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def raw_message(parts, headers=None):
    return {
        "id": "abc123",
        "threadId": "thread-1",
        "snippet": "snippet text",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": headers
            or [
                {"name": "Subject", "value": "Thank you for applying to Acme"},
                {"name": "From", "value": "Acme Recruiting <jobs@acme.com>"},
                {"name": "Date", "value": "Wed, 01 Jan 2025 10:00:00 +0000"},
            ],
            "parts": parts,
        },
    }


def test_parse_message_prefers_plain_text():
    message = parse_message(
        raw_message(
            [
                {"mimeType": "text/plain", "body": {"data": encode("plain body")}},
                {"mimeType": "text/html", "body": {"data": encode("<p>html body</p>")}},
            ]
        )
    )

    assert message.message_id == "abc123"
    assert message.thread_id == "thread-1"
    assert message.subject == "Thank you for applying to Acme"
    assert message.sender_email == "jobs@acme.com"
    assert message.sender_name == "Acme Recruiting"
    assert message.body == "plain body"
    assert message.date.year == 2025


def test_parse_message_strips_html_when_no_plain_part():
    message = parse_message(
        raw_message(
            [
                {
                    "mimeType": "text/html",
                    "body": {"data": encode("<p>Hello &amp; welcome</p><br/>Second line")},
                }
            ]
        )
    )
    assert "Hello & welcome" in message.body
    assert "<" not in message.body
    assert "Second line" in message.body


def test_parse_message_walks_nested_parts():
    message = parse_message(
        raw_message(
            [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": encode("deep body")}}
                    ],
                }
            ]
        )
    )
    assert message.body == "deep body"


def test_parse_message_handles_missing_date():
    message = parse_message(
        raw_message([], headers=[{"name": "Subject", "value": "No date here"}])
    )
    assert message.date is not None
    assert message.body == ""


def test_default_query_covers_ats_senders_and_subjects():
    query = default_query()
    assert "greenhouse.io" in query
    assert "myworkday.com" in query
    assert '"thank you for applying"' in query
    assert "subject:(application)" in query
