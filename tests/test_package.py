"""Smoke tests: the package is installed and reports its version."""

from importlib.metadata import version

import pytest

import ampere


def test_version_matches_the_package_metadata() -> None:
    assert ampere.__version__ == version("ampere")


def test_main_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    ampere.main()
    assert capsys.readouterr().out == f"ampere {ampere.__version__}\n"
