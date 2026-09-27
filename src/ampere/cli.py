"""Command line of the project: `ampere <command>`."""

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from ampere import __version__

log = logging.getLogger("ampere")


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

    data = commands.add_parser("data", help="manage the data folder: AMPERE_DATA, or data/")
    actions = data.add_subparsers(dest="action", required=True)
    actions.add_parser("init", help="create the raw layer, once, before the first ingestion")

    ingest = commands.add_parser(
        "ingest", help="fetch a source into the raw layer, then rebuild and check its clean data"
    )
    ingest.add_argument("source", choices=["smard", "eco2mix"])
    ingest.add_argument(
        "--full",
        action="store_true",
        help="ask again for the whole history since 1 July 2023, not only what can still change "
        "(about 170 requests for SMARD, 80 for éCO2mix)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a command; its return value is the exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "data":
        return init_data()
    if args.command == "ingest":
        return ingest_source(args.source, full=args.full)
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
    return 0


def init_data() -> int:
    from ampere.data.folders import data_root
    from ampere.data.raw import RawStore

    store = RawStore.create(data_root() / "raw")
    print(f"Raw layer ready in {store.root}")
    return 0


def ingest_source(name: str, *, full: bool) -> int:
    """Ingest a source; invalid rows and errors make the exit status 1."""
    from ampere.data.folders import data_root
    from ampere.data.http import client
    from ampere.data.raw import RawStore
    from ampere.sources import eco2mix, smard

    ingest = {"smard": smard.ingest, "eco2mix": eco2mix.ingest}[name]

    root = data_root()
    log.info("data folder: %s", root)
    try:
        store = RawStore(root / "raw")
    except FileNotFoundError as error:
        log.error("%s", error)
        return 1
    with client() as http:
        report = ingest(http, store, root / "clean", now=datetime.now(UTC), full=full)
    for warning in report.warnings:
        log.warning("%s", warning)
    for problem in [*report.invalid, *report.errors]:
        log.error("%s", problem)
    log.info(
        "%s: %d invalid rows, %d errors, %d warnings",
        name,
        len(report.invalid),
        len(report.errors),
        len(report.warnings),
    )
    return 1 if report.failed else 0
