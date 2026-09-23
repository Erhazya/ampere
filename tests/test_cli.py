"""The `ampere` command line."""

import pytest

import ampere
from ampere import cli


def test_version_option_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"ampere {ampere.__version__}\n"


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2


def test_api_command_serves_on_localhost_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: calls.append({"app": app, **options}))
    cli.main(["api"])
    assert calls == [{"app": "ampere.api:app", "host": "127.0.0.1", "port": 8000}]
