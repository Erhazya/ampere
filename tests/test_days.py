"""Paris days, bounded in UTC and counted in quarter-hours."""

from datetime import UTC, date, datetime, timedelta

import pytest

from ampere.data.days import day_bounds, quarter_hours


@pytest.mark.parametrize(
    ("day", "count"),
    [
        (date(2026, 6, 1), 96),
        (date(2026, 1, 15), 96),
        (date(2026, 3, 29), 92),  # clocks go forward: 02:00 becomes 03:00
        (date(2025, 10, 26), 100),  # clocks go back: 03:00 becomes 02:00
        (date(2026, 10, 25), 100),
    ],
)
def test_a_paris_day_counts_its_quarter_hours(day: date, count: int) -> None:
    assert quarter_hours(day) == count


def test_a_summer_day_starts_at_22_00_utc_the_evening_before() -> None:
    assert day_bounds(date(2026, 6, 1)) == (
        datetime(2026, 5, 31, 22, tzinfo=UTC),
        datetime(2026, 6, 1, 22, tzinfo=UTC),
    )


def test_a_winter_day_starts_at_23_00_utc_the_evening_before() -> None:
    assert day_bounds(date(2026, 1, 15)) == (
        datetime(2026, 1, 14, 23, tzinfo=UTC),
        datetime(2026, 1, 15, 23, tzinfo=UTC),
    )


def test_the_bounds_of_a_changeover_day_are_23_hours_apart() -> None:
    start, end = day_bounds(date(2026, 3, 29))
    assert end - start == timedelta(hours=23)
    assert start.tzinfo is UTC
