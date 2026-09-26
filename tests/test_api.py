"""The HTTP API, tested in memory with FastAPI's test client."""

import mimetypes
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route, WebSocketRoute

import ampere
from ampere.api import create_app

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
