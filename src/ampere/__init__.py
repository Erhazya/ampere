"""Ampère: a digital twin of a neighbourhood's energy, fed with real public data."""

from importlib.metadata import version

__version__ = version("ampere")


def main() -> None:
    """Entry point of the `ampere` command; subcommands arrive with the next increments."""
    print(f"ampere {__version__}")
