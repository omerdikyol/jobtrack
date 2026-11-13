"""Minimal read-only Gmail client: search, fetch, decode."""

from __future__ import annotations

import base64
import html
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator

import httplib2
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from jobtrack.models import EmailMessage

# A broad-but-targeted net: recruiting platforms plus the subject lines that
# job-related mail almost always uses. Chesterton's fence: this query is the
# difference between "a few hundred results" and "your whole inbox".
ATS_SENDERS = (
    "greenhouse.io",
    "lever.co",
    "myworkday.com",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "icims.com",
    "taleo.net",
    "workable.com",
    "jobvite.com",
    "hirevue.com",
    "hackerrank.com",
    "codility.com",
    "codesignal.com",
    "successfactors.com",
    "workatastartup.com",
    "wellfound.com",
    "recruitee.com",
    "breezy.hr",
    "jazzhr.com",
    "paylocity.com",
    "ultipro.com",
    "applytojob.com",
    "teamtailor.com",
    "eightfold.ai",
    "phenompeople.com",
    # Mail-sending domains of the same platforms, and assessment vendors.
    "icims.eu",
    "workablemail.com",
    "teamtailor-mail.com",
    "greenhouse-mail.io",
    "successfactors.eu",
    "manatal.com",
    "peoplise.com",
    "hrpanda.co",
    "uplico.com",
    "kula.ai",
    "ceipalmail.com",
    "testgorilla.com",
    "wonderlic.com",
    "hackerearth.com",
)

SUBJECT_PHRASES = (
    "thank you for applying",
    "thanks for applying",
    "application received",
    "your application",
    "application to",
    "application for",
    "we received your application",
    "interview",
    "coding challenge",
    "online assessment",
    "take-home",
    "next steps",
    "job offer",
    "you applied",
    "application was sent",
    "application sent",
    "başvurunuz",
    "başvurunuzu aldık",
    "iş başvurusu",
    "offer letter",
    "unfortunately",
    "not moving forward",
    "other candidates",
    "recruiter",
    "your candidacy",
    "assessment",
    "recruitment",
    "recruiting",
    "candidate",
    "başvuru",
    "başvurun",
    "başvurunu",
    "başvurunuzu",
    "değerlendirme",
    "mülakat",
    "görüşme",
    "iş teklifi",
    "işe alım",
    "pozisyon",
)

# Phrases searched anywhere in the message. Employers often use a bland
# subject ("Status for Software Engineer at X") and put the outcome only in
# the body, in either language.
BODY_PHRASES = (
    "your application",
    "thank you for applying",
    "thanks for applying",
    "time to apply",
    "your candidacy",
    "recruitment process",
    "regret to inform",
    "not been selected",
    "assesment",
    "başvurunuz",
    "başvurunuzu",
    "başvurun",
    "başvuruna",
    "başvurduğunuz",
    "pozisyonuna",
    "işe alım",
)

_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_BR_RE = re.compile(r"<br\s*/?>|</(?:p|div|tr|h[1-6]|li|td|section)>", re.I)
# LinkedIn mail links the job card: .../jobs/view/<id>/ or .../jobs/view/<slug>-<id>/
_JOB_URL_RE = re.compile(
    r"linkedin\.com/(?:comm/)?jobs/view/(?:[\w%.~-]*?-)?(\d{6,})", re.I
)


def find_job_url(raw_html: str) -> str:
    """Canonical posting URL of the first LinkedIn job link in an HTML body."""
    match = _JOB_URL_RE.search(html.unescape(raw_html))
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}/" if match else ""


_INVISIBLE_RE = re.compile(r"[\u034f\u200b\u2060\ufeff]")


def default_query() -> str:
    """Build the Gmail search used when the caller does not supply one."""
    subject = " OR ".join(f'"{phrase}"' for phrase in SUBJECT_PHRASES)
    senders = " OR ".join(f"from:{domain}" for domain in ATS_SENDERS)
    body = " OR ".join(f'"{phrase}"' for phrase in BODY_PHRASES)
    return f"(subject:({subject}) OR subject:(application) OR {senders} OR {body})"


# What the user's own applications look like in Subject lines. Deliberately
# separate from SUBJECT_PHRASES: a confirmation is written in the third person
# by the employer, while these are written by the person applying.
SENT_SUBJECT_PHRASES = (
    "application",
    "applying",
    "applied",
    "candidacy",
    "referral",
    "resume",
    "cv",
    "cover letter",
    "iş başvurusu",
    "özgeçmiş",
)


def sent_query(date_clause: str = "") -> str:
    """Search for mail the mailbox owner sent, which covers their applications.

    Joined with ``and`` to the caller's clause, because a reply inside an
    existing thread is the only way to reach that application: the employer's
    own confirmation and the user's application share one thread.
    """
    subject = " OR ".join(f'"{phrase}"' for phrase in SENT_SUBJECT_PHRASES)
    query = f"from:me (subject:({subject}))"
    date_clause = date_clause.strip()
    return f"{query} {date_clause}" if date_clause else query


def _decode(data: str) -> str:
    return _INVISIBLE_RE.sub(
        "",
        base64.urlsafe_b64decode(data.encode("utf-8")).decode(
            "utf-8", errors="replace"
        ),
    )


def _strip_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = _BR_RE.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _INVISIBLE_RE.sub("", html.unescape(text))
    return re.sub(r"[ \t]+", " ", text).strip()


def _find_job_url(payload: dict) -> str:
    for part in [payload, *(payload.get("parts") or [])]:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            if url := find_job_url(_decode(part["body"]["data"])):
                return url
        if part is not payload and (url := _find_job_url(part)):
            return url
    return ""


def _extract_body(payload: dict, prefer_html: bool = False) -> str:
    """Walk the MIME tree and return the most useful text body."""
    plain: list[str] = []
    rich: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data:
            if mime == "text/plain":
                text = _decode(data)
                # Some senders label a full HTML document as text/plain.
                if re.match(r"\s*<(?:!doctype|html)\b", text, re.I):
                    text = _strip_html(text)
                plain.append(text)
            elif mime == "text/html":
                rich.append(_strip_html(_decode(data)))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if prefer_html and rich:
        # LinkedIn's plain-text notifications sometimes contain only a footer;
        # the actual application update and role are in the HTML part.
        return "\n".join(rich)
    if plain:
        return "\n".join(plain)
    if rich:
        return "\n".join(rich)
    return ""


def _headers(payload: dict) -> dict[str, str]:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in payload.get("headers", []) or []
    }


def _parse_date(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_message(raw: dict) -> EmailMessage:
    """Convert a Gmail API message resource into an EmailMessage."""
    payload = raw.get("payload", {}) or {}
    headers = _headers(payload)
    date = _parse_date(headers.get("date", ""))
    try:
        # Google's receipt time preserves close arrivals even when senders' clocks differ.
        if raw.get("internalDate") is not None:
            date = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    from_linkedin = bool(
        re.search(r"@(?:[\w.-]+\.)?linkedin\.com(?:>|\s|$)", headers.get("from", ""), re.I)
    )
    return EmailMessage(
        job_url=_find_job_url(payload) if from_linkedin else "",
        message_id=raw.get("id", ""),
        thread_id=raw.get("threadId", ""),
        subject=headers.get("subject", "") or "",
        sender=headers.get("from", "") or "",
        to=headers.get("to", "") or "",
        date=date,
        snippet=_INVISIBLE_RE.sub("", html.unescape(raw.get("snippet", "") or "")),
        body=_extract_body(
            payload,
            prefer_html=bool(
                re.search(
                    r"@(?:[\w.-]+\.)?linkedin\.com(?:>|\s|$)",
                    headers.get("from", ""),
                    re.I,
                )
            ),
        ),
    )


class GmailClient:
    """Thin wrapper over the Gmail REST API."""

    def __init__(self, credentials, user_id: str = "me"):
        # httplib2 has no default timeout: a stalled connection hung a sync for
        # hours. Every request fails after 60 s instead and goes through _retry.
        self.service = build(
            "gmail",
            "v1",
            http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=60)),
            cache_discovery=False,
        )
        self.user_id = user_id

    def _retry(self, request, attempts: int = 4):
        delay = 1.5
        for attempt in range(attempts):
            try:
                return request.execute()
            except HttpError as exc:
                status = getattr(exc.resp, "status", None)
                quota = False
                if status == 403:
                    try:
                        reasons = {
                            e.get("reason")
                            for e in json.loads(exc.content)
                            .get("error", {})
                            .get("errors", [])
                        }
                        quota = bool(
                            reasons & {"rateLimitExceeded", "userRateLimitExceeded"}
                        )
                    except (ValueError, TypeError, AttributeError):
                        pass
                if (status in (429, 500, 502, 503) or quota) and attempt < attempts - 1:
                    if quota:
                        delay = max(delay, 15.0)
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            except (TimeoutError, ConnectionError, httplib2.HttpLib2Error):
                # A stalled or dropped connection (see the timeout in __init__).
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    def iter_message_ids(self, query: str, limit: int = 0) -> Iterator[str]:
        """Yield message ids matching ``query``, newest first."""
        page_token = None
        seen = 0
        while True:
            request = (
                self.service.users()
                .messages()
                .list(
                    userId=self.user_id,
                    q=query,
                    maxResults=100,
                    pageToken=page_token,
                )
            )
            response = self._retry(request)
            for item in response.get("messages", []) or []:
                yield item["id"]
                seen += 1
                if limit and seen >= limit:
                    return
            page_token = response.get("nextPageToken")
            if not page_token:
                return

    def get_message(self, message_id: str) -> EmailMessage:
        request = (
            self.service.users()
            .messages()
            .get(userId=self.user_id, id=message_id, format="full")
        )
        return parse_message(self._retry(request))

    def iter_messages(self, query: str, limit: int = 0) -> Iterator[EmailMessage]:
        for message_id in self.iter_message_ids(query, limit=limit):
            yield self.get_message(message_id)

    def profile_email(self) -> str:
        request = self.service.users().getProfile(userId=self.user_id)
        return self._retry(request).get("emailAddress", "")
