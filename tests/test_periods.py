"""The periods of ADR 007, and the guard that keeps the test period out of every analysis."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from ampere.data.periods import (
    TEST_END,
    TEST_FIRST_DAY,
    TEST_LAST_DAY,
    TEST_START,
    ReservedPeriodError,
    scan,
)

MICROSECOND = timedelta(microseconds=1)


@pytest.fixture
def table(tmp_path: Path) -> Path:
    """A clean table with one value an hour before, at the start of, inside, at the end of and
    after the test period, and a day column for the same instants in Paris."""
    instants = [
        TEST_START - timedelta(hours=1),
        TEST_START,
        TEST_START + timedelta(days=100),
        TEST_END - timedelta(hours=1),
        TEST_END,
    ]
    path = tmp_path / "table.parquet"
    pl.DataFrame(
        {
            "start": instants,
            "day": [
                date(2025, 6, 30),
                date(2025, 7, 1),
                date(2025, 10, 9),
                date(2026, 6, 30),
                date(2026, 7, 1),
            ],
            "value": [1.0, 2.0, 3.0, 4.0, 5.0],
            "received_at": [datetime(2026, 9, 28, tzinfo=UTC)] * 5,
        },
        schema={
            "start": pl.Datetime("us", "UTC"),
            "day": pl.Date(),
            "value": pl.Float64(),
            "received_at": pl.Datetime("us", "UTC"),
        },
    ).write_parquet(path)
    return path


def test_the_test_period_runs_over_the_paris_days_of_adr_007() -> None:
    assert (date(2025, 7, 1), date(2026, 6, 30)) == (TEST_FIRST_DAY, TEST_LAST_DAY)
    # Midnight in Paris, summer time: 22:00 UTC the day before.
    assert datetime(2025, 6, 30, 22, tzinfo=UTC) == TEST_START
    assert datetime(2026, 6, 30, 22, tzinfo=UTC) == TEST_END


def test_reads_the_rows_of_a_range_before_or_after_the_test_period(table: Path) -> None:
    before = scan(table, "start", datetime(2025, 1, 1, tzinfo=UTC), TEST_START).collect()
    assert before["value"].to_list() == [1.0]
    after = scan(table, "start", TEST_END, datetime(2027, 1, 1, tzinfo=UTC)).collect()
    assert after["value"].to_list() == [5.0]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        # A range that ends a microsecond into the test period, starts a microsecond before its
        # end, lies inside it, or spans it.
        (datetime(2025, 1, 1, tzinfo=UTC), TEST_START + MICROSECOND),
        (TEST_END - MICROSECOND, datetime(2027, 1, 1, tzinfo=UTC)),
        (TEST_START + timedelta(days=1), TEST_START + timedelta(days=2)),
        (datetime(2025, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
    ],
)
def test_refuses_a_range_that_touches_the_test_period(
    table: Path, start: datetime, end: datetime
) -> None:
    with pytest.raises(ReservedPeriodError, match="test period"):
        scan(table, "start", start, end)


def test_reads_a_table_of_paris_days_by_their_dates(table: Path) -> None:
    before = scan(table, "day", date(2025, 6, 1), TEST_FIRST_DAY).collect()
    assert before["value"].to_list() == [1.0]
    after = scan(table, "day", TEST_LAST_DAY + timedelta(days=1), date(2027, 1, 1)).collect()
    assert after["value"].to_list() == [5.0]
    with pytest.raises(ReservedPeriodError):
        scan(table, "day", date(2025, 6, 1), TEST_FIRST_DAY + timedelta(days=1))
    with pytest.raises(ReservedPeriodError):
        scan(table, "day", TEST_LAST_DAY, date(2027, 1, 1))


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (datetime(2025, 1, 1), datetime(2025, 2, 1), "time zone"),
        (datetime(2025, 1, 1, tzinfo=UTC), date(2025, 2, 1), "both instants or both days"),
        (date(2025, 1, 1), datetime(2025, 2, 1, tzinfo=UTC), "both instants or both days"),
        (datetime(2025, 2, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC), "before its end"),
        (date(2025, 2, 1), date(2025, 1, 1), "before its end"),
    ],
)
def test_refuses_a_range_it_cannot_place(
    table: Path, start: datetime | date, end: datetime | date, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        scan(table, "start", start, end)


@pytest.mark.parametrize(
    ("column", "start", "end", "message"),
    [
        # Paris days on a column of instants: Polars would read them as midnight UTC, two hours
        # into the test period.
        ("start", date(2025, 6, 1), TEST_FIRST_DAY, "not days"),
        ("day", datetime(2025, 1, 1, tzinfo=UTC), TEST_START, "not UTC instants"),
        ("time", datetime(2025, 1, 1, tzinfo=UTC), TEST_START, "no column"),
        # Every row was received in September 2026: by reception, the test period would come too.
        ("received_at", datetime(2026, 7, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC), "axis"),
        ("value", datetime(2025, 1, 1, tzinfo=UTC), TEST_START, "axis"),
    ],
)
def test_refuses_a_column_of_another_kind_than_its_bounds(
    table: Path, column: str, start: datetime | date, end: datetime | date, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        scan(table, column, start, end)


def test_refuses_instants_without_the_utc_time_zone(tmp_path: Path) -> None:
    path = tmp_path / "naive.parquet"
    pl.DataFrame({"start": [datetime(2025, 1, 1)]}).write_parquet(path)
    with pytest.raises(TypeError, match="not UTC instants"):
        scan(path, "start", datetime(2025, 1, 1, tzinfo=UTC), TEST_START)


def test_keeps_the_last_hour_of_the_test_period_out_of_the_weather_after_it(tmp_path: Path) -> None:
    # Open-Meteo labels a mean over an hour with the end of that hour (ADR 025): the value at
    # TEST_END is the mean of the last hour of the test period.
    path = tmp_path / "weather.parquet"
    pl.DataFrame(
        {"time": [TEST_END, TEST_END + timedelta(hours=1)], "value": [1.0, 2.0]},
        schema={"time": pl.Datetime("us", "UTC"), "value": pl.Float64()},
    ).write_parquet(path)
    later = datetime(2027, 1, 1, tzinfo=UTC)
    with pytest.raises(ReservedPeriodError):
        scan(path, "time", TEST_END, later)
    assert scan(path, "time", TEST_END + timedelta(hours=1), later).collect()[
        "value"
    ].to_list() == [2.0]
