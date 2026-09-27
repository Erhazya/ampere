"""The shape of a source's responses: what every source checks before it reads a value."""

import io
import json
from typing import Any, TypeGuard

import polars as pl


class SchemaError(ValueError):
    """A response that no longer has the shape the code expects."""


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


def is_int(value: object) -> TypeGuard[int]:
    """An integer, and not a boolean, which Python also takes for one."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: object) -> TypeGuard[int | float]:
    """An integer or a float, and not a boolean."""
    return isinstance(value, int | float) and not isinstance(value, bool)
