"""The shape of a source's responses: what every source checks before it reads a value."""

import json
from typing import Any


class SchemaError(ValueError):
    """A response that no longer has the shape the code expects."""


def load_json(content: bytes, url: str) -> Any:
    """The JSON of a response; a SchemaError, with its address, if it is not JSON."""
    try:
        return json.loads(content)
    except (ValueError, RecursionError) as error:
        raise SchemaError(f"{url}: not JSON ({error})") from error


def is_int(value: object) -> bool:
    """An integer, and not a boolean, which Python also takes for one."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: object) -> bool:
    """An integer or a float, and not a boolean."""
    return isinstance(value, int | float) and not isinstance(value, bool)
