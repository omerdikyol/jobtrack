from datetime import date, datetime, timezone

import pytest

from jobtrack.timerange import (
    as_of_bound,
    parse_date,
    parse_time_bound,
    with_time_range,
)


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2023-01-01", date(2023, 1, 1)),
        ("2023/01/01", date(2023, 1, 1)),
        ("01.01.2023", date(2023, 1, 1)),
        ("2024-6-5", date(2024, 6, 5)),
        ("  2023-01-01  ", date(2023, 1, 1)),
    ],
)
def test_parse_date_accepts_common_shapes(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "text", ["", "not a date", "2023-13-01", "2023-02-30", "01/02/03/04", "30"]
)
def test_parse_date_rejects_junk(text):
    with pytest.raises(ValueError, match="as a date"):
        parse_date(text)


def test_as_of_bound_covers_the_whole_day():
    bound = as_of_bound("2024-06-01")

    assert bound == datetime(2024, 6, 2, tzinfo=timezone.utc)
    # An event at 23:59 on the day itself is still included.
    assert datetime(2024, 6, 1, 23, 59, tzinfo=timezone.utc) < bound
    # ...and one a second into the next day is not.
    assert not datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc) < bound


# --------------------------------------------------------------------------
# Durations and clauses
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        ("30", "newer_than:30d"),
        ("30d", "newer_than:30d"),
        ("3m", "newer_than:3m"),
        ("2y", "newer_than:2y"),
        ("1D", "newer_than:1d"),
        ("0", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_lower_bounds(value, expected):
    assert parse_time_bound(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("30", "older_than:30d"),
        ("3m", "older_than:3m"),
        ("0", ""),
        (None, ""),
    ],
)
def test_upper_bounds_flip_direction(value, expected):
    assert parse_time_bound(value, upper=True) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2023-01-01", "after:2023/01/01"),
        ("2023/01/01", "after:2023/01/01"),
        ("01.01.2023", "after:2023/01/01"),
    ],
)
def test_dates_become_gmail_clauses(value, expected):
    assert parse_time_bound(value) == expected


def test_dates_become_before_clauses_when_upper():
    assert parse_time_bound("2024-06-01", upper=True) == "before:2024/06/01"


@pytest.mark.parametrize("value", ["last tuesday", "soon", "2023-13-01", "3w"])
def test_unreadable_bounds_explain_themselves(value):
    with pytest.raises(ValueError, match="date or a duration"):
        parse_time_bound(value)


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------
def test_range_keeps_the_original_filters():
    query = with_time_range("(subject:(interview) OR from:lever.co)", since="2023-01-01")

    assert query == "(subject:(interview) OR from:lever.co) after:2023/01/01"


def test_range_with_both_ends():
    query = with_time_range("subject:(offer)", since="2023-01-01", before="2024-06-01")

    assert query == "subject:(offer) after:2023/01/01 before:2024/06/01"


def test_range_with_no_bounds_is_unchanged():
    assert with_time_range("subject:(x)") == "subject:(x)"
    assert with_time_range("subject:(x)", since="0", before=None) == "subject:(x)"


def test_range_uses_durations_too():
    assert with_time_range("q", since="7") == "q newer_than:7d"
    assert with_time_range("q", since="7", before="2m") == "q newer_than:7d older_than:2m"
