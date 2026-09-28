"""HTTP API behind the dashboard.

The API declares its routes under /api, its documentation included, and keeps /healthz at the root
for the hosting platform. Online, it also serves the built dashboard (ADR 018).
"""

import hashlib
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from pydantic import ValidationError

from ampere import __version__
from ampere.data.folders import data_root
from ampere.recent import EXPORT, MAX_BYTES, Recent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

UNUSABLE = "The export of the recent days is unusable"


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check: the process is up and answering."""
    return {"status": "ok", "version": __version__}


@router.get(
    "/data/recent",
    response_model=None,
    responses={
        200: {"model": Recent, "description": "The recent days, as the daily job exported them"},
        503: {"description": "No export yet, or one of another shape"},
    },
)
def recent(request: Request) -> Response:
    """The recent days of the Data screen, as the daily job exported them (ADR 029). The file is
    checked again before it is served: the daily job reads what public sources send."""
    path = data_root() / EXPORT
    try:
        with path.open("rb") as file:
            content = file.read(MAX_BYTES + 1)
    except FileNotFoundError:
        raise HTTPException(503, "No export of the recent days yet") from None
    except OSError as error:
        log.error("%s does not read: %s", path, error)
        raise HTTPException(503, UNUSABLE) from None
    if len(content) > MAX_BYTES:
        log.error("%s is larger than %d bytes", path, MAX_BYTES)
        raise HTTPException(503, UNUSABLE)
    try:
        Recent.model_validate_json(content)
    except ValidationError as error:
        log.error("%s breaks the shape of the export: %s", path, error)
        raise HTTPException(503, UNUSABLE) from None
    # The browser asks again at each visit, and downloads the export only when it changed.
    etag = f'"{hashlib.sha256(content).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    asked = [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]
    if etag in asked or f"W/{etag}" in asked or "*" in asked:
        return Response(status_code=304, headers=headers)
    return Response(content, media_type="application/json", headers=headers)


def build_folder(path: Path) -> Path:
    """Return the dashboard's build folder as an absolute path, or refuse it with ValueError."""
    try:
        folder = path.absolute()
        index = folder / "index.html"
        # A regular file really inside the folder: the only kind the file server will send.
        if not index.is_file() or not index.resolve().is_relative_to(folder.resolve()):
            raise ValueError(
                f"no index.html in {folder}: build the dashboard first (npm run build, in web/)"
            )
        index.open("rb").close()  # proves the page can be read
        if (folder / "package.json").exists():
            raise ValueError(f"{folder} is the dashboard's source folder: give its build, web/dist")
        if (folder / "api").exists():
            raise ValueError(f"{folder / 'api'} must not be in the build: /api belongs to the API")
    except OSError as error:
        raise ValueError(f"cannot read {error.filename or path}: {error.strerror}") from None
    return folder


def revalidate(request: Request, response: Response) -> None:
    """Make browsers check a file on every visit, unless its name changes with its content."""
    if not request.url.path.startswith("/assets/"):
        response.headers["cache-control"] = "no-cache"


def create_app(dashboard: Path | None = None, *, docs: bool = True) -> FastAPI:
    """Build the API. Given the folder of the built dashboard, serve its files too.

    With docs=False, the two documentation pages are left out: they load their scripts from a
    CDN, which the online demo does not allow (ADR 021). The schema stays.
    """
    app = FastAPI(
        title="Ampère",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs" if docs else None,
        redoc_url="/api/redoc" if docs else None,
        # This page only completes OAuth2 logins from the documentation, and the API has none.
        swagger_ui_oauth2_redirect_url=None,
    )
    app.include_router(router)
    # The hosting platform checks every demo at /healthz; the dashboard asks /api/healthz.
    app.add_api_route("/healthz", healthz)
    if dashboard is not None:
        # Python knows the type of .woff2 fonts only from /etc/mime.types, which the Docker image
        # lacks: without this line, the dashboard's fonts would go out as application/octet-stream.
        mimetypes.add_type("font/woff2", ".woff2")
        # The files get a router of their own, so that its dependency, the cache rule, applies
        # to them only. FastAPI looks for a file only when no route matched, after the 405s and
        # the final-slash redirects of the API. No fallback page: an unknown path is a 404.
        files = APIRouter(dependencies=[Depends(revalidate)])
        files.frontend("/", directory=build_folder(dashboard), fallback=None, check_dir=True)
        app.include_router(files)
    return app
