"""Command line of the project: `ampere <command>`."""

import argparse
from pathlib import Path

from ampere import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ampere", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ampere {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    api = commands.add_parser("api", help="serve the HTTP API")
    # Local only by default: the hosting platform's reverse proxy is the public entry point.
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument(
        "--dashboard",
        type=Path,
        metavar="FOLDER",
        help="also serve the built dashboard from this folder, like the deployed demo "
        "(for example web/dist)",
    )
    api.add_argument(
        "--no-docs",
        dest="docs",
        action="store_false",
        help="leave out the documentation pages, /api/docs and /api/redoc, like the deployed "
        "demo; the schema stays at /api/openapi.json",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "api":
        # Imported here, so that other commands start fast.
        import uvicorn

        from ampere.api import create_app

        try:
            app = create_app(dashboard=args.dashboard, docs=args.docs)
        except ValueError as error:  # a folder that is not the dashboard's build
            parser.error(f"argument --dashboard: {error}")
        # One process. Given an app object, uvicorn cannot start more, and without workers=1
        # it would read their number from WEB_CONCURRENCY, then refuse to start.
        uvicorn.run(app, host=args.host, port=args.port, workers=1)
