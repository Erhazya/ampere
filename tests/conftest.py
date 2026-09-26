"""Fixtures shared by the tests."""

from pathlib import Path

import pytest


@pytest.fixture
def dashboard(tmp_path: Path) -> Path:
    """A folder shaped like the dashboard build.

    It has index.html, a hashed script in assets/, and favicon.svg, which Vite copies as is from
    web/public. It also has a 404.html page, which must never answer in place of the API.
    """
    (tmp_path / "index.html").write_text("<!doctype html><title>Ampère</title>", encoding="utf-8")
    (tmp_path / "404.html").write_text("<!doctype html><title>Not found</title>", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", "utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-1a2b3c.js").write_text("console.log('ampere');", encoding="utf-8")
    return tmp_path
