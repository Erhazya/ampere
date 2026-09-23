"""The HTTP API, tested in memory with FastAPI's test client."""

from fastapi.testclient import TestClient

import ampere
from ampere.api import app

client = TestClient(app)


def test_healthz_reports_ok_and_the_version() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": ampere.__version__}
