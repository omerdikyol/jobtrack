"""Read the when, where, and by-when out of interview and assessment mail.

Rules only, in English and Turkish: a date with an optional time, a relative
deadline ("within 72 hours", "7 gün içinde"), a weekday ("Tuesday at 2pm"),
the first meeting or test-platform link, and the interview format. Times are
kept as written, without a time zone, because the mail rarely states one; the
calendar file uses them as local ("floating") times.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from jobtrack.models import EmailMessage

DETAIL_KINDS = ("interview", "assessment")

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan", "ocak"),
            ("february", "feb", "şubat", "subat"),
            ("march", "mar", "mart"),
            ("april", "apr", "nisan"),
            ("may", "mayıs", "mayis"),
            ("june", "jun", "haziran"),
            ("july", "jul", "temmuz"),
            ("august", "aug", "ağustos", "agustos"),
            ("september", "sep", "sept", "eylül", "eylul"),
            ("october", "oct", "ekim"),
            ("november", "nov", "kasım", "kasim"),
            ("december", "dec", "aralık", "aralik"),
        ],
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAYS = {
    name: number
    for number, names in enumerate(
        [
            ("monday", "pazartesi"),
            ("tuesday", "salı", "sali"),
            ("wednesday", "çarşamba", "carsamba"),
            ("thursday", "perşembe", "persembe"),
            ("friday", "cuma"),
            ("saturday", "cumartesi"),
            ("sunday", "pazar"),
        ]
    )
    for name in names
}
_WEEKDAY = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))

_DATES = [
    # 2026-11-12
    (re.compile(r"\b(?P<y>20\d\d)-(?P<m>\d\d)-(?P<d>\d\d)\b"), "ymd"),
    # 12.11.2026 (day first), 11/12/2026 (month first unless the first is > 12)
    (re.compile(r"\b(?P<a>\d{1,2})(?P<sep>[./])(?P<b>\d{1,2})(?P=sep)(?P<y>20\d\d)\b"), "numeric"),
    # November 12, 2026 · Nov 12th
    (
        re.compile(
            rf"\b(?P<month>{_MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>20\d\d))?",
            re.I,
        ),
        "month_day",
    ),
    # 12 November 2026 · 12th of Nov · 12 Kasım
    (
        re.compile(
            rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<month>{_MONTH})\b\.?(?:,?\s+(?P<y>20\d\d))?",
            re.I,
        ),
        "day_month",
    ),
]
_TIME = re.compile(
    r"(?<![\d.:/])(?P<h>[01]?\d|2[0-3])(?:[:.](?P<min>[0-5]\d))?\s*(?P<ampm>[ap]\.?m\.?)?(?![\d.:/])",
    re.I,
)
_RELATIVE = re.compile(
    r"\bwithin\s+(?:the\s+next\s+)?(?P<n>\d{1,3})\s*(?P<unit>hours?|days?|business days?)\b"
    r"|\b(?P<n2>\d{1,3})\s*(?P<unit2>saat|gün|gun|iş günü|is gunu)\s+içinde\b",
    re.I,
)
_WEEKDAY_RE = re.compile(rf"\b(?:on\s+)?(?P<weekday>{_WEEKDAY})\b", re.I)
_TOMORROW = re.compile(r"\b(?:tomorrow|yarın|yarin)\b", re.I)
_DEADLINE_CUE = re.compile(
    r"(?:deadline|due|by|until|before|no later than|expires?|son tarih|kadar)\W*$",
    re.I,
)
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_MEETING_HOSTS = re.compile(
    r"(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com|teams\.live\.com|webex\.com|whereby\.com|chime\.aws)",
    re.I,
)
_SCHEDULING_HOSTS = re.compile(
    r"(?:calendly\.com|goodtime\.io|cal\.com|youcanbook\.me|calendar\.app\.google|outlook\.office\.com/bookwithme)",
    re.I,
)
_TEST_HOSTS = re.compile(
    r"(?:hackerrank\.com|codility\.com|codesignal\.com|testgorilla\.com|coderbyte\.com|"
    r"hackerearth\.com|hirevue\.com|devskiller\.com|testdome\.com|wonderlic\.com|"
    r"qualified\.io|karat\.com|mettl\.com|pymetrics\.ai)",
    re.I,
)
_FORMATS = [
    (
        "video",
        re.compile(
            r"\b(?:video (?:call|interview)|online interview|zoom|google meet|microsoft teams|"
            r"görüntülü|online görüşme|çevrim ?içi)\b",
            re.I,
        ),
    ),
    ("phone", re.compile(r"\b(?:phone (?:call|screen|interview)|telefon)\b", re.I)),
    (
        "onsite",
        re.compile(
            r"\b(?:on-?site|in[- ]person|at our office|ofisimizde|yüz ?yüze)\b", re.I
        ),
    ),
]


def _plausible(day: date, sent: date) -> bool:
    return sent - timedelta(days=1) <= day <= sent + timedelta(days=180)


def _year_for(month: int, day: int, sent: date) -> date | None:
    for year in (sent.year, sent.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= sent - timedelta(days=1):
            return candidate
    return None


def _parse_date(match: re.Match, style: str, sent: date) -> date | None:
    try:
        if style == "ymd":
            return date(int(match["y"]), int(match["m"]), int(match["d"]))
        if style == "numeric":
            a, b = int(match["a"]), int(match["b"])
            day, month = (a, b) if match["sep"] == "." or a > 12 else (b, a)
            return date(int(match["y"]), month, day)
        month = _MONTHS[match["month"].lower()]
        day = int(match["d"])
        if match["y"]:
            return date(int(match["y"]), month, day)
        return _year_for(month, day, sent)
    except (ValueError, KeyError):
        return None


def _time_near(text: str, start: int, end: int) -> str | None:
    """A clock time just after the date, else just before it."""
    for window in (text[end : end + 40], text[max(0, start - 30) : start]):
        for match in _TIME.finditer(window):
            hour = int(match["h"])
            minute = int(match["min"] or 0)
            ampm = (match["ampm"] or "").lower().replace(".", "")
            if not match["min"] and not ampm:
                continue  # a bare number is a count, not a time
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if hour > 23:
                continue
            return f"{hour:02d}:{minute:02d}"
    return None


def _stamp(day: date, clock: str | None) -> str:
    return f"{day.isoformat()}T{clock}" if clock else day.isoformat()


def extract_details(msg: EmailMessage, kind: str) -> dict | None:
    """Interview time, assessment deadline, link, and format, when stated."""
    if kind not in DETAIL_KINDS:
        return None
    text = f"{msg.subject}\n{msg.body or msg.snippet}"
    sent = msg.date.astimezone(timezone.utc).date()
    found: dict = {}

    for url in _URL.findall(text):
        url = url.rstrip(".,;")
        for link_kind, hosts in (
            ("meeting", _MEETING_HOSTS),
            ("assessment", _TEST_HOSTS),
            ("scheduling", _SCHEDULING_HOSTS),
        ):
            if hosts.search(url) and "link" not in found:
                found["link"], found["link_kind"] = url, link_kind

    for name, pattern in _FORMATS:
        if pattern.search(text):
            found["format"] = name
            break
    if "format" not in found and found.get("link_kind") == "meeting":
        found["format"] = "video"

    moments = []
    for pattern, style in _DATES:
        for match in pattern.finditer(text):
            day = _parse_date(match, style, sent)
            if day and _plausible(day, sent):
                deadline = bool(_DEADLINE_CUE.search(text[max(0, match.start() - 25) : match.start()]))
                moments.append(
                    (match.start(), day, _time_near(text, match.start(), match.end()), deadline)
                )
    if not moments:
        for pattern in (_TOMORROW, _WEEKDAY_RE):
            match = pattern.search(text)
            if not match:
                continue
            if pattern is _TOMORROW:
                day = sent + timedelta(days=1)
            else:
                ahead = (_WEEKDAYS[match["weekday"].lower()] - sent.weekday()) % 7
                day = sent + timedelta(days=ahead or 7)
            clock = _time_near(text, match.start(), match.end())
            if clock:  # a weekday with no time is too vague to schedule
                moments.append((match.start(), day, clock, False))
            break
    moments.sort(key=lambda m: m[0])

    for _, day, clock, deadline in moments:
        if kind == "assessment" or deadline:
            found.setdefault("deadline", _stamp(day, clock))
        else:
            found.setdefault("when", _stamp(day, clock))

    relative = _RELATIVE.search(text)
    if relative and "deadline" not in found:
        amount = int(relative["n"] or relative["n2"])
        unit = (relative["unit"] or relative["unit2"]).lower()
        # ponytail: business days counted as calendar days; good enough for a reminder.
        span = timedelta(hours=amount) if unit.startswith(("hour", "saat")) else timedelta(days=amount)
        due = msg.date.astimezone(timezone.utc) + span
        found["deadline"] = due.strftime("%Y-%m-%dT%H:%M")

    return found or None


def _ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ics_time(stamp: str) -> tuple[str, str, str]:
    """DTSTART, DTEND, and value type for a stored stamp."""
    if "T" in stamp:
        start = datetime.fromisoformat(stamp)
        end = start + timedelta(hours=1)
        return start.strftime("%Y%m%dT%H%M%S"), end.strftime("%Y%m%dT%H%M%S"), ""
    day = date.fromisoformat(stamp)
    return day.strftime("%Y%m%d"), (day + timedelta(days=1)).strftime("%Y%m%d"), ";VALUE=DATE"


def calendar_file(
    event_id: int, kind: str, details: dict, company: str, role: str | None
) -> str | None:
    """An .ics invitation for the interview time or assessment deadline."""
    stamp = details.get("when") or details.get("deadline")
    if not stamp:
        return None
    start, end, value = _ics_time(stamp)
    title = (
        f"Interview · {company}"
        if details.get("when")
        else f"Assessment due · {company}"
    )
    if role:
        title += f" ({role})"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//jobtrack//EN",
        "BEGIN:VEVENT",
        f"UID:jobtrack-event-{event_id}@localhost",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART{value}:{start}",
        f"DTEND{value}:{end}",
        f"SUMMARY:{_ics_text(title)}",
    ]
    if details.get("link"):
        lines += [f"URL:{details['link']}", f"LOCATION:{_ics_text(details['link'])}"]
    lines += ["DESCRIPTION:Added from jobtrack", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
