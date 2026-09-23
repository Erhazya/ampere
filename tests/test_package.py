"""Smoke tests: the package is installed and reports its version."""

from importlib.metadata import version

import ampere


def test_version_matches_the_package_metadata() -> None:
    assert ampere.__version__ == version("ampere")
