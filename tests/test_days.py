"""Paris days, bounded in UTC and counted in quarter-hours."""

from datetime import UTC, date, datetime, timedelta

import pytest

from ampere.data.days import (
    day_bounds,
    every_day,
    first_pass,
    never_happened,
    paris_day,
    paris_months,
    quarter_hours,
)


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


def test_every_day_counts_both_ends_and_nothing_backwards() -> None:
    assert list(every_day(date(2025, 10, 30), date(2025, 11, 1))) == [
        date(2025, 10, 30),
        date(2025, 10, 31),
        date(2025, 11, 1),
    ]
    assert list(every_day(date(2025, 11, 1), date(2025, 11, 1))) == [date(2025, 11, 1)]
    assert list(every_day(date(2025, 11, 1), date(2025, 10, 31))) == []


@pytest.mark.parametrize(
    ("local", "never"),
    [
        (datetime(2024, 3, 31, 2, 0), True),  # the clocks go from 02:00 to 03:00
        (datetime(2024, 3, 31, 2, 45), True),
        (datetime(2024, 3, 31, 3, 0), False),
        (datetime(2024, 3, 31, 1, 45), False),
        (datetime(2024, 10, 27, 2, 30), False),  # it happens twice, but it happens
        (datetime(2024, 6, 1, 2, 0), False),
    ],
)
def test_the_hour_skipped_in_spring_never_happened_in_paris(local: datetime, never: bool) -> None:
    assert never_happened(local) is never


def test_never_happened_takes_a_wall_clock_time() -> None:
    with pytest.raises(TypeError, match="wall-clock"):
        never_happened(datetime(2024, 3, 31, 2, tzinfo=UTC))


@pytest.mark.parametrize(
    ("instant", "first"),
    [
        (datetime(2023, 10, 29, 0, 0, tzinfo=UTC), True),  # 02:00 in Paris, summer time
        (datetime(2023, 10, 29, 0, 45, tzinfo=UTC), True),
        (datetime(2023, 10, 29, 1, 0, tzinfo=UTC), False),  # 02:00 again, winter time
        (datetime(2023, 10, 28, 23, 45, tzinfo=UTC), False),
        (datetime(2024, 3, 31, 1, 0, tzinfo=UTC), False),
        (datetime(2024, 6, 1, 0, 30, tzinfo=UTC), False),
    ],
)
def test_the_first_pass_of_the_hour_lived_twice_is_found(instant: datetime, first: bool) -> None:
    assert first_pass(instant) is first


def test_months_are_paris_months_bounded_in_utc() -> None:
    assert paris_months(date(2026, 3, 15), date(2026, 4, 2)) == [
        # 31 days less the hour of the clock change, then a summer month.
        (datetime(2026, 2, 28, 23, tzinfo=UTC), datetime(2026, 3, 31, 22, tzinfo=UTC)),
        (datetime(2026, 3, 31, 22, tzinfo=UTC), datetime(2026, 4, 30, 22, tzinfo=UTC)),
    ]
    assert paris_months(date(2025, 12, 31), date(2026, 1, 1)) == [
        (datetime(2025, 11, 30, 23, tzinfo=UTC), datetime(2025, 12, 31, 23, tzinfo=UTC)),
        (datetime(2025, 12, 31, 23, tzinfo=UTC), datetime(2026, 1, 31, 23, tzinfo=UTC)),
    ]
    assert paris_months(date(2026, 5, 2), date(2026, 4, 30)) == []
