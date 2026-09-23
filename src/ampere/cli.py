"""Command line of the project: `ampere <command>`."""

import argparse

from ampere import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ampere", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ampere {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    api = commands.add_parser("api", help="serve the HTTP API")
    # Local only by default: the hosting platform's reverse proxy is the public entry point.
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "api":
        import uvicorn  # imported here, so that other commands start fast

        uvicorn.run("ampere.api:app", host=args.host, port=args.port)
