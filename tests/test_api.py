"""The HTTP API, tested in memory with FastAPI's test client."""

import json
import mimetypes
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route, WebSocketRoute

import ampere
from ampere.api import create_app
from ampere.recent import EXPORT, MAX_BYTES

HEALTH = {"status": "ok", "version": ampere.__version__}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The API alone, as in development."""
    with TestClient(create_app()) as api:
        yield api


@pytest.fixture
def online(dashboard: Path) -> Iterator[TestClient]:
    """The API that also serves the dashboard files, as online."""
    with TestClient(create_app(dashboard=dashboard)) as api:
        yield api


@pytest.mark.parametrize("path", ["/api/healthz", "/healthz"])
def test_healthz_reports_ok_and_the_version(client: TestClient, path: str) -> None:
    # The dashboard asks /api/healthz, the hosting platform /healthz (ADR 018).
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == HEALTH


def test_every_route_but_healthz_is_under_api() -> None:
    app = create_app()
    # The routes added to the app itself, documentation included, then those of the included
    # routers, which only the published schema lists.
    paths = {
        route.path for route in app.routes if isinstance(route, Route | Mount | WebSocketRoute)
    }
    paths |= set(app.openapi()["paths"])
    assert {path for path in paths if not path.startswith("/api/")} == {"/healthz"}


def test_documentation_is_served_under_api(client: TestClient) -> None:
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/redoc").status_code == 200
    # FastAPI's default documentation paths stay free at the root.
    for path in ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]:
        assert client.get(path).status_code == 404


def test_documentation_can_be_left_out() -> None:
    # The online demo leaves it out (ADR 021): its two pages load their scripts from a CDN.
    with TestClient(create_app(docs=False)) as api:
        for path in ["/api/docs", "/api/redoc"]:
            assert api.get(path).status_code == 404
        # The schema stays public.
        assert api.get("/api/openapi.json").status_code == 200


def test_serves_only_the_api_without_a_dashboard_folder(client: TestClient) -> None:
    assert client.get("/").status_code == 404


def test_serves_the_dashboard_files(online: TestClient) -> None:
    page = online.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "<title>Ampère</title>" in page.text
    script = online.get("/assets/index-1a2b3c.js")
    assert script.status_code == 200
    assert script.text == "console.log('ampere');"
    # No fallback page, even for a browser: an unknown path is a 404.
    unknown = online.get("/unknown", headers={"accept": "text/html"})
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Not Found"}


def test_browsers_check_every_file_but_the_hashed_ones(online: TestClient) -> None:
    # The page names the hashed files of its build: an old copy would ask for deleted files.
    page = online.get("/")
    assert page.headers["cache-control"] == "no-cache"
    # A 304, when the page has not changed, repeats the rule.
    unchanged = online.get("/", headers={"if-none-match": page.headers["etag"]})
    assert unchanged.status_code == 304
    assert unchanged.headers["cache-control"] == "no-cache"
    # A file copied as is from web/public keeps its name from one build to the next.
    assert online.get("/favicon.svg").headers["cache-control"] == "no-cache"
    # A file in assets/ never changes under its name, so it keeps the default caching.
    assert "cache-control" not in online.get("/assets/index-1a2b3c.js").headers
    # The rule is for files only: the API's answers never get it.
    assert "cache-control" not in online.get("/api/healthz").headers


@pytest.fixture
def python_types_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python's own list of file types, without /etc/mime.types, as in the Docker image."""
    # mimetypes keeps its table in a private global, which mimetypes.init() only extends: a new
    # table, built without the system files, replaces it for this test.
    monkeypatch.setattr(mimetypes, "_db", mimetypes.MimeTypes())


@pytest.mark.usefixtures("python_types_only")
def test_fonts_keep_their_type_without_the_system_list(dashboard: Path) -> None:
    with TestClient(create_app(dashboard=dashboard)) as online:
        font = online.get("/assets/geist-1a2b3c.woff2")
    assert font.headers["content-type"] == "font/woff2"


def test_refuses_a_folder_that_is_not_a_build(dashboard: Path) -> None:
    (dashboard / "package.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="source folder"):
        create_app(dashboard=dashboard)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/healthz"),
        ("GET", "/healthz"),
        ("GET", "/api/docs"),
        ("POST", "/api/healthz"),  # 405, with Allow: GET
        ("HEAD", "/healthz"),  # 405 too: the routes answer GET only
        ("GET", "/api/healthz/"),  # redirected to the path without the final slash
        ("GET", "/api/missing"),  # the API's JSON 404, not the build's 404.html
    ],
)
def test_the_dashboard_files_change_no_api_answer(
    client: TestClient, online: TestClient, method: str, path: str
) -> None:
    # Sent as by a browser opening the path, the case where a fallback page could answer.
    browser = {"accept": "text/html"}
    alone = client.request(method, path, headers=browser, follow_redirects=False)
    served = online.request(method, path, headers=browser, follow_redirects=False)
    assert served.status_code == alone.status_code
    for header in ["allow", "location", "content-type", "cache-control"]:
        assert served.headers.get(header) == alone.headers.get(header)
    assert served.content == alone.content


def export(**changes: Any) -> bytes:
    """A small export of the recent days, of the shape of ADR 029."""
    content = {
        "generated_at": 1_790_596_000_000,
        "start": 1_790_028_000_000,
        "end": 1_790_805_600_000,
        "series": [
            {
                "id": "price",
                "source": "smard",
                "unit": "EUR/MWh",
                "points": [[1_790_028_000_000, 81.5], [1_790_028_900_000, -3]],
            }
        ],
        "days": [{"day": "2026-09-21", "public_holiday": None, "school_holidays": None}],
        "sources": [
            {
                "id": "smard",
                "name": "Bundesnetzagentur | SMARD.de",
                "licence": "CC BY 4.0",
                "updated_at": None,
                "received_at": 1_790_596_000_000,
            }
        ],
    }
    return json.dumps({**content, **changes}).encode()


@pytest.fixture
def exported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Where the daily job writes the export, in a data folder of its own."""
    monkeypatch.setenv("AMPERE_DATA", str(tmp_path))
    (tmp_path / EXPORT).parent.mkdir()
    return tmp_path / EXPORT


def test_the_recent_days_are_served_as_the_daily_job_exported_them(
    client: TestClient, exported: Path
) -> None:
    exported.write_bytes(export())
    response = client.get("/api/data/recent")
    assert response.status_code == 200
    assert response.content == export()
    assert response.headers["content-type"] == "application/json"
    # The browser asks again at each visit, and downloads the export only when it changed.
    assert response.headers["cache-control"] == "no-cache"
    etag = response.headers["etag"]
    again = client.get("/api/data/recent", headers={"If-None-Match": etag})
    assert (again.status_code, again.content) == (304, b"")
    assert again.headers["etag"] == etag
    exported.write_bytes(export(generated_at=1_790_682_400_000))
    changed = client.get("/api/data/recent", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag


def test_without_an_export_the_recent_days_are_unavailable(
    client: TestClient, exported: Path
) -> None:
    response = client.get("/api/data/recent")
    assert response.status_code == 503
    assert response.json() == {"detail": "No export of the recent days yet"}


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (b"not json", "breaks the shape"),
        (export(extra=1), "breaks the shape"),
        (
            export(
                series=[{"id": "price", "source": "smard", "unit": "EUR/MWh", "points": [[1, 2]]}]
            ),
            "breaks the shape",
        ),
        (
            export(
                days=[{"day": "2026-09-21", "public_holiday": "x" * 101, "school_holidays": None}]
            ),
            "breaks the shape",
        ),
        (export().replace(b"81.5", b"NaN"), "breaks the shape"),
        (b" " * MAX_BYTES + export(), f"is larger than {MAX_BYTES} bytes"),
    ],
    ids=["not JSON", "an unknown field", "an instant before 2000", "a long name", "NaN", "too big"],
)
def test_an_export_of_another_shape_is_not_served(
    client: TestClient,
    exported: Path,
    caplog: pytest.LogCaptureFixture,
    body: bytes,
    why: str,
) -> None:
    exported.write_bytes(body)
    response = client.get("/api/data/recent")
    assert response.status_code == 503
    assert response.json() == {"detail": "The export of the recent days is unusable"}
    # The log says which file, and why.
    assert f"{exported} {why}" in caplog.text
