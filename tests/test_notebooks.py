"""The notebooks as they are in the repository: the CI has no data to run them, so it checks what
they hold (ADR 030)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

NOTEBOOKS = sorted((Path(__file__).parents[1] / "notebooks").glob("*.ipynb"))
# Every way to read a table without the guard of the test period (ADR 007).
DIRECT_READS = re.compile(r"read_parquet|scan_parquet|read_ipc|scan_ipc|duckdb|pd\.read_|open\(")


def code_cells(path: Path) -> list[dict[str, Any]]:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


def source(cell: dict[str, Any]) -> str:
    text = cell["source"]
    return text if isinstance(text, str) else "".join(text)


def test_there_is_a_notebook() -> None:
    assert [path.name for path in NOTEBOOKS] == ["exploration.ipynb"]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.name)
def test_its_cells_ran_from_top_to_bottom_without_error(path: Path) -> None:
    cells = code_cells(path)
    # Run again in one go, the cells are numbered 1, 2, 3...: any other order means a hidden state.
    assert [cell["execution_count"] for cell in cells] == list(range(1, len(cells) + 1))
    errors = [
        output for cell in cells for output in cell["outputs"] if output["output_type"] == "error"
    ]
    assert errors == []


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.name)
def test_it_reads_the_data_through_the_guard_only(path: Path) -> None:
    code = "\n".join(source(cell) for cell in code_cells(path))
    assert "from ampere.data.periods import" in code
    assert DIRECT_READS.findall(code) == []
