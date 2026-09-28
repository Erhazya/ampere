"""The parts of the clean layer that every source shares."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from ampere.data.clean import UPDATES, Report, instants_per_day, write_parquet, write_updates
from ampere.data.days import day_bounds


def test_invalid_rows_and_errors_fail_a_run_but_warnings_do_not() -> None:
    assert not Report(warnings=["2025-10-09: 0 of 96 quarter-hours published so far"]).failed
    assert Report(invalid=["2025-10-07 10:00 UTC: 2 prices"]).failed
    assert Report(errors=["2025-10-07: 1 of 96 quarter-hours missing"]).failed


def test_a_clean_file_is_replaced_whole(tmp_path: Path) -> None:
    path = tmp_path / "clean" / "source" / "values.parquet"
    write_parquet(pl.DataFrame({"value": [1.0, 2.0]}), path)
    write_parquet(pl.DataFrame({"value": [3.0]}), path)
    assert pl.read_parquet(path)["value"].to_list() == [3.0]
    assert [file.name for file in path.parent.iterdir()] == ["values.parquet"]


def test_a_failed_write_keeps_the_last_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "values.parquet"
    write_parquet(pl.DataFrame({"value": [1.0, 2.0]}), path)
    before = path.read_bytes()
    write = pl.DataFrame.write_parquet

    def fail_halfway(frame: pl.DataFrame, file: Any, **options: Any) -> None:
        write(frame.head(1), file, **options)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", fail_halfway)
    with pytest.raises(OSError, match="No space left"):
        write_parquet(pl.DataFrame({"value": [3.0, 4.0]}), path)
    assert path.read_bytes() == before
    assert [file.name for file in tmp_path.iterdir()] == ["values.parquet"]


def test_quarter_hours_are_counted_per_paris_day_and_per_group() -> None:
    start = day_bounds(date(2025, 10, 26))[0]  # the clocks go back: 100 quarter-hours
    quarters = [start + i * timedelta(minutes=15) for i in range(100)]
    # In ARA, a quarter-hour twice and an instant off the grid: one quarter-hour only.
    extra = [quarters[0], quarters[0], quarters[1] + timedelta(minutes=7)]
    frame = pl.DataFrame({"area": ["FR"] * 100 + ["ARA"] * 3, "start": quarters + extra})
    counts = instants_per_day(frame, by=["area"])
    assert sorted(counts.rows()) == [
        ("ARA", date(2025, 10, 26), 1),
        ("FR", date(2025, 10, 26), 100),
    ]


def test_hours_are_counted_in_any_column() -> None:
    start = day_bounds(date(2026, 3, 29))[0]  # the clocks go forward: 23 hours
    hours = [start + i * timedelta(hours=1) for i in range(23)]
    # An instant a quarter past the hour is off this grid: it is not counted.
    frame = pl.DataFrame({"time": [*hours, hours[5] + timedelta(minutes=15)]})
    counts = instants_per_day(frame, column="time", every="1h")
    assert counts.rows() == [(date(2026, 3, 29), 23)]


def test_write_updates_keeps_the_dates_it_is_not_given(tmp_path: Path) -> None:
    path = tmp_path / "source" / "updates.parquet"
    first = datetime(2026, 9, 28, 12, tzinfo=UTC)
    write_updates(
        {
            "b": (datetime(2026, 9, 18, tzinfo=UTC), first),
            "a": (datetime(2026, 9, 1, tzinfo=UTC), first),
        },
        path,
    )
    later = first + timedelta(days=1)
    write_updates({"b": (datetime(2026, 9, 29, tzinfo=UTC), later)}, path)
    frame = pl.read_parquet(path)
    assert frame.schema == UPDATES
    assert frame.rows() == [
        ("a", datetime(2026, 9, 1, tzinfo=UTC), first),
        ("b", datetime(2026, 9, 29, tzinfo=UTC), later),
    ]


def test_write_updates_writes_over_a_file_that_does_not_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "updates.parquet"
    path.write_bytes(b"not parquet")
    moment = datetime(2026, 9, 28, 12, tzinfo=UTC)
    write_updates({"a": (moment, moment)}, path)
    assert pl.read_parquet(path).rows() == [("a", moment, moment)]
    assert "does not read" in caplog.text
