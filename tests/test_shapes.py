"""The checks every source makes on the shape of its responses."""

from datetime import UTC, datetime

import pytest

from ampere.sources.shapes import utc_instant


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
