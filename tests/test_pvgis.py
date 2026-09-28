"""PVGIS against a fake of its API: the hourly production of 2023 of a roof of 1 kWp at Lyon, for
three orientations (ADR 032)."""

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import polars as pl
import pytest

from ampere.cli import ARCHIVES, SOURCES
from ampere.data.clean import Report
from ampere.data.http import client
from ampere.data.raw import RawStore
from ampere.sources.pvgis import HOURS, ORIENTATIONS, ingest, parse
from ampere.sources.shapes import SchemaError

NOW = datetime(2026, 9, 28, 13, tzinfo=UTC)
FIRST = datetime(2023, 1, 1, 0, 10, tzinfo=UTC)


def answer(
    aspect: int, *, hours: int = HOURS, slope: int = 30, peak: float = 1000.0
) -> dict[str, Any]:
    """An answer shaped like PVGIS's: a bell of production around noon, a little earlier for a
    roof facing east and later for one facing west."""
    rows = []
    for hour in range(hours):
        moment = FIRST + timedelta(hours=hour)
        solar = moment.hour + 0.2 - aspect / 45
        power = max(0.0, peak * math.sin(math.pi * (solar - 6) / 12)) if 6 < solar < 18 else 0.0
        rows.append({"time": moment.strftime("%Y%m%d:%H%M"), "P": round(power * 0.7, 2)})
    return {
        "inputs": {
            "location": {"latitude": 45.76, "longitude": 4.84, "elevation": 164.0},
            "meteo_data": {"radiation_db": "PVGIS-SARAH3", "year_min": 2023, "year_max": 2023},
            "mounting_system": {"fixed": {"slope": {"value": slope}, "azimuth": {"value": aspect}}},
            "pv_module": {"technology": "c-Si", "peak_power": 1.0, "system_loss": 14.0},
        },
        "outputs": {"hourly": rows},
        "meta": {"inputs": {}},
    }


class FakePvgis:
    def __init__(self) -> None:
        self.bodies: dict[int, bytes] = {
            aspect: json.dumps(answer(aspect)).encode() for aspect in ORIENTATIONS.values()
        }
        self.requests: list[dict[str, list[str]]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        query = parse_qs(urlsplit(str(request.url)).query)
        self.requests.append(query)
        aspect = int(query["aspect"][0])
        return httpx2.Response(
            200, content=self.bodies[aspect], headers={"content-type": "application/json"}
        )


@pytest.fixture
def fake() -> FakePvgis:
    return FakePvgis()


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore.create(tmp_path / "raw", clock=lambda: NOW)


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    return tmp_path / "clean"


def run(fake: FakePvgis, store: RawStore, clean: Path) -> Report:
    with client(httpx2.MockTransport(fake.handle)) as http:
        return ingest(http, store, clean, now=NOW, sleep=lambda seconds: None)


def production(clean: Path) -> pl.DataFrame:
    return pl.read_parquet(clean / "pvgis" / "production.parquet")


def test_asks_for_each_orientation_of_a_roof_of_1_kwp_at_lyon_in_2023(
    fake: FakePvgis, store: RawStore, clean: Path
) -> None:
    report = run(fake, store, clean)
    assert not report.failed, report
    assert sorted(int(query["aspect"][0]) for query in fake.requests) == [-45, 0, 45]
    for query in fake.requests:
        assert query["lat"] == ["45.76"] and query["lon"] == ["4.84"]
        assert query["startyear"] == query["endyear"] == ["2023"]
        assert query["peakpower"] == ["1"] and query["loss"] == ["14"] and query["angle"] == ["30"]
    frame = production(clean)
    assert frame.height == 3 * HOURS
    assert sorted(frame["orientation"].unique()) == ["south", "south-east", "south-west"]
    first = frame.filter(pl.col("orientation") == "south").sort("time").row(0, named=True)
    assert first["time"] == FIRST
    assert frame.schema["power_w_per_kwp"] == pl.Float64


def test_keeps_a_response_only_when_it_changes(
    fake: FakePvgis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    run(fake, store, clean)
    assert len(store.receipts("pvgis")) == 3


def test_is_not_asked_by_the_daily_job() -> None:
    # An archive that no longer changes: `ampere ingest pvgis` asks for it, the daily job never.
    assert "pvgis" in ARCHIVES
    assert "pvgis" not in SOURCES


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda body: body["inputs"]["mounting_system"]["fixed"]["azimuth"].update(value=90),
            "roof",
        ),
        (lambda body: body["inputs"]["mounting_system"]["fixed"]["slope"].update(value=35), "roof"),
        (lambda body: body["inputs"]["pv_module"].update(system_loss=10.0), "roof"),
        (lambda body: body["inputs"]["meteo_data"].update(year_max=2022), "2023"),
        (lambda body: body["outputs"].update(hourly=body["outputs"]["hourly"][:-1]), "hours"),
        (lambda body: body["outputs"]["hourly"][100].update(P=1500.0), "power"),
        (lambda body: body["outputs"]["hourly"][100].update(P=-1.0), "power"),
        (lambda body: body["outputs"]["hourly"][100].update(P="12"), "power"),
        (lambda body: body["outputs"]["hourly"][100].update(time="20230105:0310"), "hours"),
        (lambda body: body["outputs"]["hourly"][100].update(time="5 Jan"), "hours"),
    ],
)
def test_refuses_an_answer_of_another_shape(
    change: Callable[[dict[str, Any]], object], message: str
) -> None:
    body = answer(0)
    change(body)
    with pytest.raises(SchemaError, match=message):
        parse(json.dumps(body).encode(), "https://pvgis.example", "south")


def test_a_faulty_answer_leaves_the_clean_table_as_it_was(
    fake: FakePvgis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    before = production(clean)
    fake.bodies[45] = json.dumps(answer(45, hours=10)).encode()
    report = run(fake, store, clean)
    # The faulty answer is kept, as every answer is; the older one still reads.
    assert report.failed
    assert any("south-west" in error for error in report.errors)
    assert production(clean).equals(before)
