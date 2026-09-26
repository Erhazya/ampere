"""The `ampere` command line."""

import os
from collections.abc import Callable
from pathlib import Path
from unittest.mock import ANY, Mock

import pytest
from fastapi.testclient import TestClient

import ampere
from ampere import cli


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """uvicorn.run, replaced by a mock that records the call instead of starting a server."""
    mock = Mock()
    monkeypatch.setattr("uvicorn.run", mock)
    return mock


def test_version_option_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"ampere {ampere.__version__}\n"


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2


def test_api_command_serves_the_api_alone_on_localhost_by_default(run: Mock) -> None:
    cli.main(["api"])
    run.assert_called_once_with(ANY, host="127.0.0.1", port=8000, workers=1)
    api = TestClient(run.call_args.args[0])
    assert api.get("/api/healthz").status_code == 200
    assert api.get("/").status_code == 404  # no dashboard files


def test_api_command_passes_its_options_on(run: Mock, dashboard: Path) -> None:
    cli.main(["api", "--host", "0.0.0.0", "--port", "9000", "--dashboard", str(dashboard)])
    run.assert_called_once_with(ANY, host="0.0.0.0", port=9000, workers=1)
    assert TestClient(run.call_args.args[0]).get("/").status_code == 200


def remove_index(build: Path) -> None:
    (build / "index.html").unlink()


def turn_index_into_a_folder(build: Path) -> None:
    (build / "index.html").unlink()
    (build / "index.html").mkdir()


def link_index_outside(build: Path) -> None:
    # The file server never follows a link out of the folder.
    outside = build.parent / f"{build.name}-outside.html"
    outside.write_text("<!doctype html>", encoding="utf-8")
    (build / "index.html").unlink()
    (build / "index.html").symlink_to(outside)


def make_index_unreadable(build: Path) -> None:
    (build / "index.html").chmod(0)


def add_package_json(build: Path) -> None:
    (build / "package.json").write_text("{}", encoding="utf-8")


def add_api_folder(build: Path) -> None:
    (build / "api").mkdir()


@pytest.mark.parametrize(
    ("spoil", "message"),
    [
        (remove_index, "no index.html in"),
        (turn_index_into_a_folder, "no index.html in"),
        (link_index_outside, "no index.html in"),
        pytest.param(
            make_index_unreadable,
            "cannot read",
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file"),
        ),
        (add_package_json, "is the dashboard's source folder"),
        (add_api_folder, "/api belongs to the API"),
    ],
)
def test_api_command_refuses_a_folder_that_is_not_a_build(
    run: Mock,
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
    run.assert_not_called()


def test_api_command_names_the_folder_in_full(
    run: Mock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A relative path depends on the working directory: the message shows where it looked.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["api", "--dashboard", "web/dist"])
    assert f"no index.html in {Path.cwd() / 'web' / 'dist'}:" in capsys.readouterr().err
    run.assert_not_called()
