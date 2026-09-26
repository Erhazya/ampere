"""Command line of the project: `ampere <command>`."""

import argparse
from pathlib import Path

from ampere import __version__


def dashboard_folder(value: str) -> Path:
    """Check, before the server starts, that the folder holds a built dashboard."""
    folder = Path(value).absolute()
    index = folder / "index.html"
    try:
        index.read_bytes()  # a small file: reading it proves it is there and readable
    except FileNotFoundError:
        raise argparse.ArgumentTypeError(
            f"no index.html in {folder}: build the dashboard first (npm run build, in web/)"
        ) from None
    except OSError as error:
        raise argparse.ArgumentTypeError(f"cannot read {index}: {error.strerror}") from None
    if (folder / "package.json").exists():
        raise argparse.ArgumentTypeError(
            f"{folder} is the dashboard's source folder: give its build instead, web/dist"
        )
    if (folder / "api").exists():
        raise argparse.ArgumentTypeError(
            f"{folder / 'api'} must not be in the build: /api belongs to the API"
        )
    return folder


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
        type=dashboard_folder,
        metavar="FOLDER",
        help="also serve the built dashboard from this folder, like the deployed demo "
        "(for example web/dist)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "api":
        # Imported here, so that other commands start fast.
        import uvicorn

        from ampere.api import create_app

        uvicorn.run(create_app(dashboard=args.dashboard), host=args.host, port=args.port)
