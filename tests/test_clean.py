"""The parts of the clean layer that every source shares."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from ampere.data.clean import Report, quarter_hours_per_day, write_parquet
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
    counts = quarter_hours_per_day(frame, by=["area"])
    assert sorted(counts.rows()) == [
        ("ARA", date(2025, 10, 26), 1),
        ("FR", date(2025, 10, 26), 100),
    ]
