"""Parsing for the date and duration values users type.

Two separate jobs live here:

* turning a bound into a Gmail search clause, so ``sync --since 2023-01-01``
  searches from that date instead of only "the last N days";
* turning a date into the exclusive upper bound used to replay history, so
  ``list --as-of 2024-06-01`` can show a past state.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y")
_FORMATS_HELP = "2023-01-01, 2023/01/01 or 01.01.2023"

_RELATIVE = re.compile(r"^(\d+)\s*(d|m|y)?$", re.I)


def parse_date(value: str) -> date:
    """Parse a calendar date. Raises ValueError with the accepted formats."""
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not read {value!r} as a date. Try {_FORMATS_HELP}.")


def as_of_bound(value: str) -> datetime:
    """Exclusive upper bound that covers the whole of ``value``, in UTC.

    Events are stored in UTC, so a date-only bound means "through the end of
    that day", expressed as the instant the next day begins.
    """
    start = datetime.combine(parse_date(value), time.min, tzinfo=timezone.utc)
    return start + timedelta(days=1)


def parse_time_bound(value: str | None, *, upper: bool = False) -> str:
    """Turn a user-supplied bound into a Gmail search clause.

    ``30`` / ``30d`` -> ``newer_than:30d``      ``3m`` / ``2y`` -> ``newer_than:3m``
    ``2023-01-01``   -> ``after:2023/01/01``

    With ``upper=True`` the direction flips to ``older_than`` / ``before``.
    Returns "" when there is no bound.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    relative = _RELATIVE.fullmatch(text)
    if relative:
        amount, unit = int(relative.group(1)), (relative.group(2) or "d").lower()
        if amount == 0:
            return ""  # "0" means no limit
        keyword = "older_than" if upper else "newer_than"
        return f"{keyword}:{amount}{unit}"

    try:
        parsed = parse_date(text)
    except ValueError:
        raise ValueError(
            f"Could not read {value!r} as a date or a duration. "
            f"Try 30, 3m, 2y, or {_FORMATS_HELP}."
        ) from None

    keyword = "before" if upper else "after"
    return f"{keyword}:{parsed:%Y/%m/%d}"


def with_time_range(
    query: str, since: str | None = None, before: str | None = None
) -> str:
    """Append Gmail time bounds to a query, keeping any existing filters."""
    parts = [query]
    for value, upper in ((since, False), (before, True)):
        clause = parse_time_bound(value, upper=upper)
        if clause:
            parts.append(clause)
    return " ".join(part for part in parts if part)
