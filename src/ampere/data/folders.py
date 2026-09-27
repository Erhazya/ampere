"""Where the data lives (ADR 022): AMPERE_DATA, or data/ in the working folder."""

import os
from pathlib import Path


def data_root() -> Path:
    """The data folder as an absolute path, so that the logs say exactly where it is."""
    return Path(os.environ.get("AMPERE_DATA") or "data").absolute()
