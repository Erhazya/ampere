"""The `ampere` command line."""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ampere
from ampere import cli


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """The calls to uvicorn.run, recorded instead of starting a server."""
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: calls.append({"app": app, **options}))
    return calls


@pytest.fixture
def never_served(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if the command starts the server."""
    monkeypatch.setattr("uvicorn.run", lambda app, **options: pytest.fail("the server started"))


def test_version_option_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"ampere {ampere.__version__}\n"


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2


def test_api_command_serves_on_localhost_by_default(served: list[dict[str, object]]) -> None:
    cli.main(["api"])
    assert len(served) == 1
    call = served[0]
    assert (call["host"], call["port"]) == ("127.0.0.1", 8000)
    app = call["app"]
    assert isinstance(app, FastAPI)
    # The API alone: no dashboard files at the root.
    assert TestClient(app).get("/").status_code == 404


def test_api_command_passes_its_options_on(
    served: list[dict[str, object]], dashboard: Path
) -> None:
    cli.main(["api", "--host", "0.0.0.0", "--port", "9000", "--dashboard", str(dashboard)])
    assert len(served) == 1
    call = served[0]
    assert (call["host"], call["port"]) == ("0.0.0.0", 9000)
    app = call["app"]
    assert isinstance(app, FastAPI)
    assert TestClient(app).get("/").status_code == 200


def remove_index(build: Path) -> None:
    (build / "index.html").unlink()


def turn_index_into_a_folder(build: Path) -> None:
    (build / "index.html").unlink()
    (build / "index.html").mkdir()


def add_package_json(build: Path) -> None:
    (build / "package.json").write_text("{}", encoding="utf-8")


def add_api_folder(build: Path) -> None:
    (build / "api").mkdir()


@pytest.mark.usefixtures("never_served")
@pytest.mark.parametrize(
    ("spoil", "message"),
    [
        (remove_index, "no index.html in"),
        (turn_index_into_a_folder, "cannot read"),
        (add_package_json, "is the dashboard's source folder"),
        (add_api_folder, "/api belongs to the API"),
    ],
)
def test_api_command_refuses_a_folder_that_is_not_a_build(
    capsys: pytest.CaptureFixture[str],
    dashboard: Path,
    spoil: Callable[[Path], None],
    message: str,
) -> None:
    spoil(dashboard)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["api", "--dashboard", str(dashboard)])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.usefixtures("never_served")
def test_api_command_names_the_folder_in_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A relative path depends on the working directory: the message shows where it looked.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["api", "--dashboard", "web/dist"])
    assert f"no index.html in {Path.cwd() / 'web' / 'dist'}:" in capsys.readouterr().err
