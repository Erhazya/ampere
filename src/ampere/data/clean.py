"""The clean layer: what every source writes and checks the same way (ADR 022, 023 and 024).

A source rebuilds its clean file from the raw layer at each run, checks it, and replaces it only
when its rows are valid.
"""

import io
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import polars as pl

from ampere.data.days import PARIS
from ampere.data.raw import write_atomically

log = logging.getLogger(__name__)

# The date each dataset was last updated, as its source publishes it, and the reception of the
# response that says so: the Licence Ouverte asks to cite that date with the data (ADR 029).
UPDATES = pl.Schema(
    {
        "dataset": pl.String(),
        "updated_at": pl.Datetime("us", "UTC"),
        "received_at": pl.Datetime("us", "UTC"),
    }
)


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


def write_updates(
    dates: Mapping[str, tuple[datetime, datetime]], path: Path, *, known: Collection[str]
) -> None:
    """Record, for each dataset, the date its source last updated it and the reception of the
    first response that gave that date. The other known datasets keep their row, since a source
    may give some dates and fail to give others; a dataset the source no longer asks for leaves
    the file."""
    rows = {}
    if path.exists():
        try:
            kept = pl.read_parquet(path)
            rows = {row[0]: (row[1], row[2]) for row in kept.select(UPDATES.names()).rows()}
        # Polars may even panic on a damaged file, and its panic is a BaseException.
        except (OSError, pl.exceptions.PolarsError, pl.exceptions.PanicException) as error:
            log.warning("%s does not read (%s): it is written again", path, error)
    rows = {name: moments for name, moments in (rows | dict(dates)).items() if name in known}
    frame = pl.DataFrame(
        [(name, *moments) for name, moments in sorted(rows.items())], schema=UPDATES, orient="row"
    )
    write_parquet(frame, path)


def instants_per_day(
    frame: pl.DataFrame, by: Sequence[str] = (), *, column: str = "start", every: str = "15m"
) -> pl.DataFrame:
    """How many distinct instants of a grid each Paris day has in a column, for each group of
    `by`: by default, the quarter-hours of `start`.

    The result has the columns of `by`, then `day` and `len`. An instant off the grid is not
    counted: it would hide a missing one.
    """
    on_grid = frame.filter(pl.col(column).dt.truncate(every) == pl.col(column))
    day = pl.col(column).dt.convert_time_zone(PARIS.key).dt.date().alias("day")
    return on_grid.select(*by, column).unique().group_by(*by, day).len()
