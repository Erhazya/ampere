"""The checks every source makes on the shape of its responses."""

from datetime import UTC, datetime, timedelta

import pytest

from ampere.sources.shapes import update_date, utc_instant


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-28T02:00:00+02:00", datetime(2026, 9, 28, tzinfo=UTC)),
        ("2026-09-28T00:00:00Z", datetime(2026, 9, 28, tzinfo=UTC)),
        ("2026-03-29T01:30:00+00:00", datetime(2026, 3, 29, 1, 30, tzinfo=UTC)),
    ],
)
def test_an_iso_instant_with_its_offset_is_read_in_utc(value: str, expected: datetime) -> None:
    moment = utc_instant(value)
    assert moment == expected
    assert moment is not None and moment.tzinfo is UTC


@pytest.mark.parametrize(
    "value",
    [
        # Without an offset, the instant could be in any time zone.
        "2026-09-28T02:00:00",
        "2026-09-28",
        "yesterday",
        "",
        None,
        1790546400,
        # Beyond the calendar of Python once in UTC.
        "9999-12-31T23:59:59-01:00",
    ],
)
def test_anything_else_is_no_instant(value: object) -> None:
    assert utc_instant(value) is None


NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-18T08:35:17+00:00", datetime(2026, 9, 18, 8, 35, 17, tzinfo=UTC)),
        # From the first instant of 2000 to the day after the run, both included.
        ("2000-01-01T00:00:00+00:00", datetime(2000, 1, 1, tzinfo=UTC)),
        ((NOW + timedelta(days=1)).isoformat(), NOW + timedelta(days=1)),
        ("1999-12-31T23:59:59+00:00", None),
        ("1970-01-01T00:00:00+00:00", None),
        ((NOW + timedelta(days=1, seconds=1)).isoformat(), None),
        ("2026-09-18T08:35:17", None),
        (None, None),
    ],
)
def test_a_date_of_last_update_lies_between_2000_and_the_day_after_the_run(
    value: object, expected: datetime | None
) -> None:
    assert update_date(value, NOW) == expected
