"""The `ampere` command line."""

import logging
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import ANY, Mock

import pytest
from fastapi.testclient import TestClient

import ampere
from ampere import cli
from ampere.sources import smard


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
    assert api.get("/api/docs").status_code == 200
    assert api.get("/").status_code == 404  # no dashboard files


def test_api_command_passes_its_options_on(run: Mock, dashboard: Path) -> None:
    cli.main(["api", "--host", "0.0.0.0", "--port", "9000", "--dashboard", str(dashboard)])
    run.assert_called_once_with(ANY, host="0.0.0.0", port=9000, workers=1)
    assert TestClient(run.call_args.args[0]).get("/").status_code == 200


def test_no_docs_option_leaves_the_documentation_out(run: Mock) -> None:
    cli.main(["api", "--no-docs"])
    api = TestClient(run.call_args.args[0])
    assert api.get("/api/docs").status_code == 404
    assert api.get("/api/openapi.json").status_code == 200


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


def test_data_init_creates_the_raw_layer_in_ampere_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMPERE_DATA", str(tmp_path / "store"))
    assert cli.main(["data", "init"]) == 0
    assert (tmp_path / "store" / "raw" / ".ampere-raw").is_file()
    assert str(tmp_path / "store" / "raw") in capsys.readouterr().out


def test_the_data_folder_is_data_in_the_working_folder_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AMPERE_DATA", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["data", "init"]) == 0
    assert cli.main(["data", "init"]) == 0  # a second time keeps the layer
    assert (tmp_path / "data" / "raw" / ".ampere-raw").is_file()
    assert capsys.readouterr().out == f"Raw layer ready in {tmp_path / 'data' / 'raw'}\n" * 2


def test_ingest_needs_a_raw_layer_created_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AMPERE_DATA", str(tmp_path / "unmounted"))
    assert cli.main(["ingest", "smard"]) == 1
    # A wrong path or a missing volume is as likely as a first use.
    assert "check AMPERE_DATA and its volume" in caplog.text
    assert "ampere data init" in caplog.text
    assert not (tmp_path / "unmounted").exists()


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A data folder in AMPERE_DATA, with its raw layer."""
    monkeypatch.setenv("AMPERE_DATA", str(tmp_path))
    cli.main(["data", "init"])
    return tmp_path


def fake_ingest(monkeypatch: pytest.MonkeyPatch, report: smard.Report) -> list[dict[str, object]]:
    """Replace smard.ingest with a fake that records its arguments and returns the report."""
    calls: list[dict[str, object]] = []

    def ingest(http: object, store: object, clean: Path, **options: object) -> smard.Report:
        calls.append({"clean": clean, **options})
        return report

    monkeypatch.setattr(smard, "ingest", ingest)
    return calls


def test_ingest_passes_the_time_of_the_run_in_utc_and_the_full_option(
    monkeypatch: pytest.MonkeyPatch, data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ampere")
    calls = fake_ingest(monkeypatch, smard.Report())
    assert cli.main(["ingest", "smard"]) == 0
    assert cli.main(["ingest", "smard", "--full"]) == 0
    first, second = calls
    assert first["clean"] == data / "clean"
    now = first["now"]
    assert isinstance(now, datetime) and now.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - now) < timedelta(minutes=1)
    assert first["full"] is False and second["full"] is True
    # The history starts where ADR 023 says, and no option shortens it.
    assert "since" not in first
    assert date(2023, 7, 1) == smard.SINCE
    assert f"data folder: {data}" in caplog.text
    assert "smard: 0 invalid rows, 0 errors, 0 warnings" in caplog.text


def test_ingest_has_no_option_to_shorten_the_history() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["ingest", "smard", "--since", "2025-09-22"])
    assert exit_info.value.code == 2


@pytest.mark.parametrize(
    ("invalid", "errors", "status"),
    [
        ([], [], 0),
        ([], ["2025-10-07: 1 of 96 quarter-hours missing"], 1),
        (["2025-10-07 10:00 UTC: 2 prices"], [], 1),
    ],
    ids=["no problem", "a gap", "an invalid row"],
)
def test_ingest_fails_when_the_checks_find_problems(
    monkeypatch: pytest.MonkeyPatch,
    data: Path,
    caplog: pytest.LogCaptureFixture,
    invalid: list[str],
    errors: list[str],
    status: int,
) -> None:
    warnings = ["2025-10-09: 0 of 96 published so far"]
    fake_ingest(monkeypatch, smard.Report(invalid=invalid, errors=errors, warnings=warnings))
    assert cli.main(["ingest", "smard"]) == status
    assert ("ampere", logging.WARNING, warnings[0]) in caplog.record_tuples
    for problem in [*invalid, *errors]:
        assert ("ampere", logging.ERROR, problem) in caplog.record_tuples


def test_a_failed_ingestion_goes_up_instead_of_exiting_with_0(
    monkeypatch: pytest.MonkeyPatch, data: Path
) -> None:
    def ingest(*args: object, **options: object) -> smard.Report:
        raise smard.SchemaError("https://www.smard.de/app/chart_data/254/DE/x.json: no series")

    monkeypatch.setattr(smard, "ingest", ingest)
    with pytest.raises(smard.SchemaError):
        cli.main(["ingest", "smard"])
