"""The shape of a source's responses: what every source checks before it reads a value."""

import io
import json
import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, TypeGuard

import polars as pl


class SchemaError(ValueError):
    """A response that no longer has the shape the code expects."""


# A date of last update before 2000, such as the start of the Unix epoch, is faulty metadata.
EARLIEST_UPDATE = datetime(2000, 1, 1, tzinfo=UTC)


def utc_instant(value: object) -> datetime | None:
    """A string in ISO 8601 with its offset, as an instant in UTC; None for anything else, an
    instant without an offset or beyond the calendar of Python once in UTC included."""
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
            if moment.utcoffset() is not None:
                return moment.astimezone(UTC)
        except (ValueError, OverflowError):
            pass
    return None


def update_date(value: object, now: datetime) -> datetime | None:
    """A date of last update that a source publishes, in UTC; None when it is faulty: no instant
    with its offset, before 2000, or later than the day after the run, as with the publications
    of Enedis (ADR 026)."""
    moment = utc_instant(value)
    if moment is None or not EARLIEST_UPDATE <= moment <= now + timedelta(days=1):
        return None
    return moment


def load_json(content: bytes, url: str) -> Any:
    """The JSON of a response; a SchemaError, with its address, if it is not JSON."""
    try:
        return json.loads(content)
    except (ValueError, RecursionError) as error:
        raise SchemaError(f"{url}: not JSON ({error})") from error


def parquet_footer(content: bytes, url: str) -> tuple[int, pl.Schema]:
    """The number of rows and the columns of a Parquet response, read from its footer alone:
    Parquet compresses by itself, and a few kilobytes could unfold into gigabytes of rows."""
    try:
        scan = pl.scan_parquet(io.BytesIO(content))
        return scan.select(pl.len()).collect().item(), scan.collect_schema()
    # Polars may even panic on a damaged file, and its panic is a BaseException: caught here, it
    # makes the response faulty instead of stopping every run that reads it again.
    except (pl.exceptions.PolarsError, pl.exceptions.PanicException, OSError) as error:
        raise SchemaError(f"{url}: not a Parquet export ({error})") from error


def read_parquet(
    content: bytes,
    url: str,
    columns: Mapping[str, Callable[[pl.DataType], bool]],
    *,
    max_rows: int,
    max_columns: int,
    holds: str,
) -> pl.DataFrame:
    """The columns a Parquet response must have, loaded alone, after a check of its footer: its
    number of rows and of columns, then the type of each column, tested by its function."""
    rows, schema = parquet_footer(content, url)
    if rows > max_rows or len(schema) > max_columns:
        raise SchemaError(f"{url}: {rows} rows in {len(schema)} columns, more than {holds}")
    for column, fits in columns.items():
        dtype = schema.get(column)
        if dtype is None or not fits(dtype):
            raise SchemaError(f"{url}: no column {column} of the expected type, but {dtype}")
    try:
        return pl.read_parquet(io.BytesIO(content), columns=list(columns))
    except (pl.exceptions.PolarsError, pl.exceptions.PanicException, OSError) as error:
        raise SchemaError(f"{url}: not a Parquet export ({error})") from error


def is_int(value: object) -> TypeGuard[int]:
    """An integer, and not a boolean, which Python also takes for one."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: object) -> TypeGuard[int | float]:
    """An integer or a float, and not a boolean."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def finite(value: object) -> float | None:
    """A JSON number as a float; None for anything else, or for a number that is not finite."""
    if not is_number(value):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer too large for a float
        return None
    return number if math.isfinite(number) else None
