"""HTTP API behind the dashboard."""

from fastapi import FastAPI

from ampere import __version__

app = FastAPI(title="Ampère", version=__version__)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check, polled by the hosting platform: the process is up and answering."""
    return {"status": "ok", "version": __version__}
