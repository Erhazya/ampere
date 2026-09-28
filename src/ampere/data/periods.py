"""The test period of ADR 007, and the guard that keeps it out of every analysis.

The test period is seen once, at the end, by the final evaluation. Any other code that analyses
the clean layer, a notebook or a model, reads it through scan(), which refuses a range that touches
the test period. The ingestion and the export of the Data screen read every period, but analyse
nothing.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from ampere.data.days import day_bounds

# The Paris days of the test period, both included, and the same period in UTC instants.
TEST_FIRST_DAY = date(2025, 7, 1)
TEST_LAST_DAY = date(2026, 6, 30)
TEST_START = day_bounds(TEST_FIRST_DAY)[0]
TEST_END = day_bounds(TEST_LAST_DAY)[1]


class ReservedPeriodError(ValueError):
    """A read that would reach the test period, which only the final evaluation may read."""


def scan(path: Path, column: str, start: datetime | date, end: datetime | date) -> pl.LazyFrame:
    """The rows of a clean table from start to end, excluded, by the values of `column`, as a lazy
    frame: Polars then reads only what it needs. Start and end are both UTC instants, for a table
    of instants, or both Paris days, for a table of days such as the calendars, and the column
    holds the same. A range that touches the test period is refused (ADR 007)."""
    # A datetime is also a date for Python: an instant must not pass for a day, nor the reverse.
    instants = isinstance(start, datetime), isinstance(end, datetime)
    if instants[0] != instants[1]:
        raise TypeError("a range is made of both instants or both days, not one of each")
    if isinstance(start, datetime) and isinstance(end, datetime):
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ValueError("an instant needs a time zone: without one, it could be any time")
        reserved: tuple[datetime | date, datetime | date] = (TEST_START, TEST_END)
    else:
        reserved = (TEST_FIRST_DAY, TEST_LAST_DAY + timedelta(days=1))
    if not start < end:
        raise ValueError(f"a range starts before its end: {start} to {end}")
    if start < reserved[1] and reserved[0] < end:
        raise ReservedPeriodError(
            f"{start} to {end} touches the test period, {reserved[0]} to {reserved[1]}: only the"
            " final evaluation reads it (ADR 007)"
        )
    frame = pl.scan_parquet(path)
    # Only the footer is read. Polars compares a column of UTC instants with a day at midnight
    # UTC, two hours into the Paris day: the column must hold what its bounds are.
    kind = frame.collect_schema().get(column)
    if kind is None:
        raise ValueError(f"{path} has no column {column}")
    if isinstance(start, datetime):
        if not (isinstance(kind, pl.Datetime) and kind.time_zone == "UTC"):
            raise TypeError(
                f"{column} holds {kind}, not UTC instants: give it days, if it holds days"
            )
    elif not isinstance(kind, pl.Date):
        raise TypeError(f"{column} holds {kind}, not days: give it UTC instants, if it holds them")
    return frame.filter((pl.col(column) >= start) & (pl.col(column) < end))
