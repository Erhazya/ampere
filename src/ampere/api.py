"""HTTP API behind the dashboard.

The API declares its routes under /api, its documentation included, and keeps /healthz at the root
for the hosting platform. Online, it also serves the built dashboard (ADR 018).
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request, Response

from ampere import __version__

router = APIRouter(prefix="/api")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check: the process is up and answering."""
    return {"status": "ok", "version": __version__}


async def revalidate_pages(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Make browsers check an HTML page on every visit: it names the hashed files of its build."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("cache-control", "no-cache")
    return response


def create_app(dashboard: Path | None = None) -> FastAPI:
    """Build the API. Given the folder of the built dashboard, serve its files too."""
    app = FastAPI(
        title="Ampère",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        # This page only completes OAuth2 logins from the documentation, and the API has none.
        swagger_ui_oauth2_redirect_url=None,
    )
    app.include_router(router)
    # The hosting platform checks every demo at /healthz; the dashboard asks /api/healthz.
    app.add_api_route("/healthz", healthz)
    if dashboard is not None:
        # FastAPI looks for a file only when no route matched, after the 405s and the
        # final-slash redirects of the API. No fallback page: an unknown path is a 404.
        app.frontend("/", directory=dashboard, fallback=None, check_dir=True)
        app.middleware("http")(revalidate_pages)
    return app
