"""Paris days, bounded in UTC and counted in quarter-hours."""

from datetime import UTC, date, datetime, timedelta

import pytest

from ampere.data.days import day_bounds, paris_day, quarter_hours


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


def test_both_bounds_are_in_utc() -> None:
    # Two aware times of the same instant are equal whatever their zones: check the zone too.
    start, end = day_bounds(date(2026, 6, 1))
    assert start.tzinfo is UTC and end.tzinfo is UTC


@pytest.mark.parametrize("year", [2025, 2026])
def test_the_days_of_a_year_follow_each_other_without_gaps(year: int) -> None:
    days = [
        date(year, 1, 1) + timedelta(days=n)
        for n in range((date(year + 1, 1, 1) - date(year, 1, 1)).days)
    ]
    for day in days:
        assert day_bounds(day)[1] == day_bounds(day + timedelta(days=1))[0]
    # The four quarter-hours lost in March come back in October.
    assert sum(quarter_hours(day) for day in days) == 96 * len(days)


@pytest.mark.parametrize(
    ("instant", "day"),
    [
        (datetime(2026, 6, 1, 21, 59, tzinfo=UTC), date(2026, 6, 1)),
        (datetime(2026, 6, 1, 22, 0, tzinfo=UTC), date(2026, 6, 2)),  # midnight in Paris (UTC+2)
        (datetime(2026, 1, 15, 22, 59, tzinfo=UTC), date(2026, 1, 15)),
        (datetime(2026, 1, 15, 23, 0, tzinfo=UTC), date(2026, 1, 16)),  # midnight in Paris (UTC+1)
    ],
)
def test_an_instant_belongs_to_its_paris_day(instant: datetime, day: date) -> None:
    assert paris_day(instant) == day


def test_an_instant_without_a_time_zone_has_no_paris_day() -> None:
    with pytest.raises(ValueError, match="time zone"):
        paris_day(datetime(2026, 6, 1, 22, 30))


def test_a_day_is_a_date_not_an_instant() -> None:
    # A datetime is a date for Python: its calendar date would silently give the wrong Paris day.
    with pytest.raises(TypeError, match="paris_day"):
        day_bounds(datetime(2026, 6, 1, 22, 30, tzinfo=UTC))
    with pytest.raises(TypeError, match="paris_day"):
        quarter_hours(datetime(2026, 6, 1, 22, 30, tzinfo=UTC))
