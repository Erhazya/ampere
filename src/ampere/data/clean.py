"""The clean layer: what every source writes and checks the same way (ADR 022, 023 and 024).

A source rebuilds its clean file from the raw layer at each run, checks it, and replaces it only
when its rows are valid.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from ampere.data.days import PARIS
from ampere.data.raw import write_atomically


@dataclass
class Report:
    """What the checks of a source found.

    Invalid rows break a rule of the clean file, which then stays as it was. Invalid rows and
    errors make the run fail; warnings are only logged.
    """

    invalid: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.invalid or self.errors)


def write_parquet(frame: pl.DataFrame, path: Path) -> None:
    """Replace a clean file whole, even across a power cut: a reader sees the old file or the new
    one, never half of it, and a failed write leaves nothing behind."""
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    write_atomically(path, buffer.getvalue())


def quarter_hours_per_day(frame: pl.DataFrame, by: Sequence[str] = ()) -> pl.DataFrame:
    """How many distinct quarter-hours of the grid each Paris day has, for each group of `by`.

    The result has the columns of `by`, then `day` and `len`. An instant off the grid is not
    counted: it would hide a missing quarter-hour.
    """
    on_grid = frame.filter(pl.col("start").dt.truncate("15m") == pl.col("start"))
    day = pl.col("start").dt.convert_time_zone(PARIS.key).dt.date().alias("day")
    return on_grid.select(*by, "start").unique().group_by(*by, day).len()
