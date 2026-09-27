"""Open-Meteo against a fake Open-Meteo: the archive and the single runs of ECMWF IFS."""

import json
import logging
import random
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import polars as pl
import pytest

from ampere.data.clean import Report
from ampere.data.days import day_bounds
from ampere.data.http import UnexpectedResponse, client
from ampere.data.raw import RawStore
from ampere.sources.openmeteo import (
    FORECAST,
    OBSERVED,
    SCHEMA,
    Request,
    check,
    fingerprint,
    ingest,
    months,
    parse,
    runs,
)
from ampere.sources.shapes import SchemaError

PARIS = ZoneInfo("Europe/Paris")
HOUR = timedelta(hours=1)
SINCE = date(2026, 4, 1)
FIRST_RUN = date(2026, 6, 10)
NOW = datetime(2026, 6, 20, 15, tzinfo=PARIS)  # a Saturday afternoon, 13:00 UTC
LATER = NOW + timedelta(days=1)
# A run comes 6 h 30 after its launch.
ARRIVAL = timedelta(hours=6, minutes=30)
UNITS = {
    "temperature_2m": "°C",
    "shortwave_radiation": "W/m²",
    "direct_radiation": "W/m²",
    "diffuse_radiation": "W/m²",
    "direct_normal_irradiance": "W/m²",
    "wind_speed_10m": "m/s",
}
RADIATIONS = (
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
)
NAMES = (
    "temperature_c",
    "global_w_m2",
    "direct_horizontal_w_m2",
    "diffuse_w_m2",
    "direct_normal_w_m2",
    "wind_speed_m_s",
)
MEANS = ("global_w_m2", "direct_horizontal_w_m2", "diffuse_w_m2", "direct_normal_w_m2")


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def sun(moment: datetime) -> float:
    """A radiation: the mean of the hour that ends at `moment`, nothing at night."""
    return float(max(0, 6 - abs(moment.hour - 12)) * 100)


FIELDS: dict[str, Callable[[datetime], float]] = {
    "temperature_2m": lambda moment: 10.0 + moment.hour / 2,
    "shortwave_radiation": sun,
    "direct_radiation": lambda moment: sun(moment) * 0.6,
    "diffuse_radiation": lambda moment: sun(moment) * 0.4,
    "direct_normal_irradiance": lambda moment: sun(moment) * 0.9,
    "wind_speed_10m": lambda moment: 3.0,
}
# A forecast adds this to each value, so that a test sees where a value comes from.
FORECAST_OFFSET = 0.5


def answer(content: bytes) -> httpx2.Response:
    return httpx2.Response(
        200, content=content, headers={"content-type": "application/json; charset=utf-8"}
    )


def refusal(reason: str) -> httpx2.Response:
    return httpx2.Response(400, json={"reason": reason, "error": True})


class FakeOpenMeteo:
    """Open-Meteo's archive and single runs, as they behave.

    The archive answers up to today, and fills the hours after `now` with the forecast. A run
    exists from `first_run`, 6 h 30 after its launch, and has no radiation for the hour before
    its launch. Each response has a new generationtime_ms, and its keys come in either order.
    """

    def __init__(self) -> None:
        self.now = NOW
        self.first_run = FIRST_RUN
        self.missing_runs: set[date] = set()
        # Values that come back null: (dataset, field, hour).
        self.gaps: set[tuple[str, str, datetime]] = set()
        # What the archive adds to its values, when a test revises them.
        self.revision = 0.0
        self.bodies: dict[str, bytes] = {}
        self.statuses: dict[str, int] = {}
        self.requests: list[str] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.statuses:
            return httpx2.Response(self.statuses[url], json={"reason": "refused", "error": True})
        if url in self.bodies:
            return answer(self.bodies[url])
        params = request.url.params
        assert (params["latitude"], params["longitude"]) == ("45.76", "4.84")
        assert params["hourly"] == ",".join(UNITS)
        assert (params["models"], params["timezone"]) == ("ecmwf_ifs", "GMT")
        assert params["wind_speed_unit"] == "ms"
        today = self.now.astimezone(UTC).date()
        where = (request.url.host, request.url.path)
        if where == ("archive-api.open-meteo.com", "/v1/archive"):
            first = date.fromisoformat(params["start_date"])
            last = date.fromisoformat(params["end_date"])
            if last > today:
                return refusal(
                    f"Parameter 'end_date' is out of allowed range from 1940-01-01 to {today}"
                )
            start = datetime.combine(first, time(), UTC)
            return answer(self.body(OBSERVED, start, 24 * ((last - first).days + 1)))
        if where == ("single-runs-api.open-meteo.com", "/v1/forecast"):
            assert params["forecast_days"] == "2"
            launch = datetime.fromisoformat(params["run"]).replace(tzinfo=UTC)
            if (
                launch.date() < self.first_run
                or launch.date() in self.missing_runs
                or self.now < launch + ARRIVAL
            ):
                return refusal(
                    "The requested model run is not available. "
                    f"Model: ecmwf_ifs, run: {params['run']}Z"
                )
            return answer(self.body(FORECAST, launch, 48))
        return httpx2.Response(404)

    def body(self, kind: str, start: datetime, hours: int) -> bytes:
        moments = [start + i * HOUR for i in range(hours)]
        hourly: dict[str, list[object]] = {
            "time": [f"{moment:%Y-%m-%dT%H:%M}" for moment in moments]
        }
        for field in UNITS:
            hourly[field] = [self.value(kind, field, start, moment) for moment in moments]
        body = {
            "latitude": 45.729347,
            "longitude": 4.8264985,
            "generationtime_ms": random.Random(len(self.requests)).uniform(0.1, 200),
            "utc_offset_seconds": 0,
            "timezone": "GMT",
            "timezone_abbreviation": "GMT",
            "elevation": 161.0,
            "hourly_units": {"time": "iso8601", **UNITS},
            "hourly": hourly,
        }
        if len(self.requests) % 2:
            body = dict(reversed(body.items()))
        return json.dumps(body).encode()

    def value(self, kind: str, field: str, start: datetime, moment: datetime) -> float | None:
        if (kind, field, moment) in self.gaps:
            return None
        if kind == FORECAST and field in RADIATIONS and moment == start:
            return None  # no mean for the hour before the launch
        if kind == FORECAST or moment > self.now:  # the archive fills the rest of today
            return FIELDS[field](moment) + FORECAST_OFFSET
        return FIELDS[field](moment) + self.revision


@pytest.fixture
def fake() -> FakeOpenMeteo:
    return FakeOpenMeteo()


@pytest.fixture
def store(tmp_path: Path, fake: FakeOpenMeteo) -> RawStore:
    # Responses are received at the time the fake lives in.
    return RawStore.create(tmp_path / "raw", clock=lambda: fake.now)


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    return tmp_path / "clean"


def run(
    fake: FakeOpenMeteo,
    store: RawStore,
    clean: Path,
    now: datetime = NOW,
    full: bool = False,
    pauses: list[float] | None = None,
) -> Report:
    fake.now = now
    with client(httpx2.MockTransport(fake.handle)) as http:
        sleep = pauses.append if pauses is not None else (lambda seconds: None)
        return ingest(
            http, store, clean, now=now, since=SINCE, first_run=FIRST_RUN, full=full, sleep=sleep
        )


def weather(clean: Path) -> pl.DataFrame:
    return pl.read_parquet(clean / "openmeteo" / "weather.parquet")


def value(
    frame: pl.DataFrame, variable: str, moment: datetime, run: datetime | None = None
) -> float:
    """A value observed, or forecast by a run."""
    origin = pl.col("run").is_null() if run is None else pl.col("run") == run
    rows = frame.filter((pl.col("variable") == variable) & (pl.col("time") == moment) & origin)
    assert rows.height == 1, rows
    assert rows["kind"][0] == (OBSERVED if run is None else FORECAST)
    return float(rows["value"][0])


def asked(fake: FakeOpenMeteo) -> list[str]:
    """The requests, named "observed 2026-06-01 2026-06-20" or "run 2026-06-20T00:00"."""
    names = []
    for url in map(httpx2.URL, fake.requests):
        if url.host == "archive-api.open-meteo.com":
            names.append(f"observed {url.params['start_date']} {url.params['end_date']}")
        else:
            names.append(f"run {url.params['run']}")
    return names


def month_url(first: date, last: date) -> str:
    (request,) = months(first, last)
    return request.url


FIRST_ASKED = [
    "observed 2026-03-31 2026-03-31",
    "observed 2026-04-01 2026-04-30",
    "observed 2026-05-01 2026-05-31",
    "observed 2026-06-01 2026-06-20",
    *(f"run 2026-06-{day}T00:00" for day in range(10, 21)),
]


def test_the_urls_ask_for_lyon_the_six_variables_in_utc_and_the_wind_in_m_s() -> None:
    (month,) = months(date(2026, 6, 1), date(2026, 6, 20))
    (launch,) = runs(date(2026, 6, 20), date(2026, 6, 20))
    common = f"hourly={','.join(UNITS)}&models=ecmwf_ifs&timezone=GMT&wind_speed_unit=ms"
    assert month.url == (
        "https://archive-api.open-meteo.com/v1/archive?latitude=45.76&longitude=4.84"
        f"&start_date=2026-06-01&end_date=2026-06-20&{common}"
    )
    assert launch.url == (
        "https://single-runs-api.open-meteo.com/v1/forecast?latitude=45.76&longitude=4.84"
        f"&run=2026-06-20T00:00&forecast_days=2&{common}"
    )


def test_months_are_utc_months_the_first_from_its_first_day_the_last_up_to_today() -> None:
    requests = months(date(2025, 11, 30), date(2026, 1, 10))
    assert [(r.kind, r.name, r.start, r.end, r.run) for r in requests] == [
        (OBSERVED, "2025-11", utc(2025, 11, 30), utc(2025, 12, 1), None),
        (OBSERVED, "2025-12", utc(2025, 12, 1), utc(2026, 1, 1), None),
        (OBSERVED, "2026-01", utc(2026, 1, 1), utc(2026, 2, 1), None),
    ]
    asked_days = [
        (httpx2.URL(r.url).params["start_date"], httpx2.URL(r.url).params["end_date"])
        for r in requests
    ]
    assert asked_days == [
        ("2025-11-30", "2025-11-30"),
        ("2025-12-01", "2025-12-31"),
        ("2026-01-01", "2026-01-10"),
    ]


def test_runs_are_the_00_utc_run_of_each_day_two_days_long() -> None:
    requests = runs(date(2024, 3, 14), date(2024, 3, 15))
    assert [(r.kind, r.name, r.start, r.end, r.run) for r in requests] == [
        (FORECAST, "2024-03-14T00:00", utc(2024, 3, 14), utc(2024, 3, 16), utc(2024, 3, 14)),
        (FORECAST, "2024-03-15T00:00", utc(2024, 3, 15), utc(2024, 3, 17), utc(2024, 3, 15)),
    ]


def test_the_first_run_fetches_every_month_and_every_run(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    # No error: the first month starts on 31 March, since the Paris day of 1 April starts at
    # 22:00 UTC the day before.
    assert run(fake, store, clean) == Report()
    assert asked(fake) == FIRST_ASKED
    receipts = store.receipts("openmeteo")
    assert [(receipt.dataset, receipt.request) for receipt in receipts] == [
        (OBSERVED, "2026-03"),
        (OBSERVED, "2026-04"),
        (OBSERVED, "2026-05"),
        (OBSERVED, "2026-06"),
        *((FORECAST, f"2026-06-{day}T00:00") for day in range(10, 21)),
    ]
    # A month is filed under its name, and each response keeps its exact URL.
    assert receipts[3].url == fake.requests[3]


def test_the_clean_weather_keeps_each_hour_as_published(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    frame = weather(clean)
    assert frame.schema == SCHEMA
    noon = utc(2026, 6, 19, 12)
    assert value(frame, "temperature_c", noon) == 16.0
    # A radiation is the mean of the hour that ends at its time, 11:00 to 12:00 here.
    assert value(frame, "global_w_m2", noon) == 600.0
    assert value(frame, "direct_horizontal_w_m2", noon) == 360.0
    assert value(frame, "diffuse_w_m2", noon) == 240.0
    assert value(frame, "direct_normal_w_m2", noon) == 540.0
    assert value(frame, "wind_speed_m_s", noon) == 3.0
    launch = utc(2026, 6, 19)
    assert value(frame, "temperature_c", noon, run=launch) == 16.5
    assert frame.filter(pl.col("run") == launch)["received_at"].unique().to_list() == [NOW]


def test_a_run_has_48_hours_but_no_radiation_for_the_hour_before_its_launch(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    launch = utc(2026, 6, 19)
    forecast = weather(clean).filter(pl.col("run") == launch)
    counts = dict(forecast.group_by("variable").len().rows())
    assert counts == {name: 47 if name in MEANS else 48 for name in NAMES}
    first = forecast.filter(pl.col("time") == launch)["variable"].cast(str)
    assert sorted(first) == ["temperature_c", "wind_speed_m_s"]


def test_an_observed_value_later_than_its_reception_is_left_out(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    # The archive fills the rest of today with the forecast.
    run(fake, store, clean)
    observed = weather(clean).filter(pl.col("kind") == OBSERVED)
    assert observed["time"].max() == utc(2026, 6, 20, 13)
    assert value(observed, "temperature_c", utc(2026, 6, 20, 13)) == 16.5


def test_a_later_run_asks_only_for_the_current_month_and_the_run_of_the_day(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.requests.clear()
    assert run(fake, store, clean, now=LATER) == Report()
    assert asked(fake) == ["observed 2026-06-01 2026-06-21", "run 2026-06-21T00:00"]


@pytest.mark.parametrize(
    ("now", "again"), [(utc(2026, 6, 14, 23), True), (utc(2026, 6, 15), False)]
)
def test_a_month_can_change_until_14_days_after_its_end(
    fake: FakeOpenMeteo, store: RawStore, clean: Path, now: datetime, again: bool
) -> None:
    run(fake, store, clean, now=utc(2026, 6, 10, 12))
    fake.requests.clear()
    run(fake, store, clean, now=now)
    assert ("observed 2026-05-01 2026-05-31" in asked(fake)) is again


def test_a_response_that_differs_only_by_generationtime_ms_adds_nothing(
    fake: FakeOpenMeteo, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run(fake, store, clean)
    kept = store.receipts("openmeteo")
    caplog.set_level(logging.INFO)
    run(fake, store, clean, now=NOW + timedelta(minutes=10))
    assert store.receipts("openmeteo") == kept
    assert "openmeteo: 1 requests, 0 new responses, " in caplog.text


def test_a_revision_of_the_archive_is_kept_and_used(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.revision = 0.25
    run(fake, store, clean, now=LATER)
    frame = weather(clean)
    assert value(frame, "temperature_c", utc(2026, 6, 19, 12)) == 16.25  # asked again
    assert value(frame, "temperature_c", utc(2026, 5, 19, 12)) == 16.0  # past its 14 days
    assert len(store.receipts("openmeteo")) == len(FIRST_ASKED) + 2


def test_the_run_of_the_day_before_it_comes_is_neither_an_error_nor_a_warning(
    fake: FakeOpenMeteo, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert run(fake, store, clean, now=utc(2026, 6, 20, 6)) == Report()
    assert asked(fake)[-1] == "run 2026-06-20T00:00"
    assert "no run 2026-06-20T00:00 at Open-Meteo (HTTP 400)" in caplog.text
    assert weather(clean).filter(pl.col("run") == utc(2026, 6, 20)).is_empty()
    assert "2026-06-20T00:00" not in [r.request for r in store.receipts("openmeteo")]


@pytest.mark.parametrize(("minute", "warned"), [(11 * 60 + 59, False), (12 * 60, True)])
def test_the_run_of_the_day_still_missing_from_noon_utc_is_a_warning(
    fake: FakeOpenMeteo, store: RawStore, clean: Path, minute: int, warned: bool
) -> None:
    fake.missing_runs.add(date(2026, 6, 20))
    report = run(fake, store, clean, now=utc(2026, 6, 20) + timedelta(minutes=minute))
    assert report.errors == []
    assert report.warnings == (["run 2026-06-20T00:00: missing at 12:00 UTC"] if warned else [])


def test_a_missing_run_is_an_error_and_is_asked_again(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    fake.missing_runs.add(date(2026, 6, 12))
    assert run(fake, store, clean).errors == ["run 2026-06-12T00:00: missing"]
    fake.requests.clear()
    run(fake, store, clean, now=LATER)
    assert "run 2026-06-12T00:00" in asked(fake)


def test_a_run_with_missing_values_is_an_error_and_is_asked_again(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    fake.gaps.add((FORECAST, "temperature_2m", utc(2026, 6, 10, 5)))
    report = run(fake, store, clean)
    assert report.errors == ["run 2026-06-10T00:00 temperature_c: 1 of 48 hours missing"]
    fake.requests.clear()
    run(fake, store, clean, now=LATER)
    assert "run 2026-06-10T00:00" in asked(fake)


def test_a_month_with_missing_values_is_asked_again_until_whole(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    # Its first hour, which a run may lack for a radiation but a month may not.
    fake.gaps.add((OBSERVED, "diffuse_radiation", utc(2026, 4, 1)))
    report = run(fake, store, clean)
    assert report.errors == ["observed diffuse_w_m2 2026-04-01: 1 of 24 hours missing"]
    fake.requests.clear()
    run(fake, store, clean, now=LATER)
    assert "observed 2026-04-01 2026-04-30" in asked(fake)
    fake.gaps.clear()
    fake.requests.clear()
    assert run(fake, store, clean, now=LATER + timedelta(days=1)) == Report()
    assert "observed 2026-04-01 2026-04-30" in asked(fake)
    fake.requests.clear()
    run(fake, store, clean, now=LATER + timedelta(days=2))
    assert "observed 2026-04-01 2026-04-30" not in asked(fake)


def test_full_asks_for_every_month_and_every_run_again(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.requests.clear()
    run(fake, store, clean, full=True)
    assert asked(fake) == FIRST_ASKED


def test_the_requests_are_spaced_out(fake: FakeOpenMeteo, store: RawStore, clean: Path) -> None:
    pauses: list[float] = []
    run(fake, store, clean, pauses=pauses)
    assert pauses == [0.5] * (len(FIRST_ASKED) - 1)


def test_a_faulty_response_is_an_error_and_the_run_goes_on(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    april = month_url(date(2026, 4, 1), date(2026, 4, 30))
    fake.bodies[april] = b"[]"
    report = run(fake, store, clean)
    assert report.errors[0] == f"{april}: not an object"
    assert "observed temperature_c 2026-04-10: 24 of 24 hours missing" in report.errors
    assert asked(fake) == FIRST_ASKED
    assert value(weather(clean), "temperature_c", utc(2026, 5, 19, 12)) == 16.0


def test_a_faulty_response_falls_back_on_the_last_readable_one(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    june = month_url(date(2026, 6, 1), date(2026, 6, 21))
    fake.bodies[june] = b"[]"
    report = run(fake, store, clean, now=LATER)
    assert report.errors[0] == f"{june}: not an object"
    assert report.warnings == [
        "observed 2026-06: the response received at 2026-06-20 13:00:00 UTC is used, "
        "the later ones are unusable"
    ]
    # The older response keeps its values up to its own reception.
    observed = weather(clean).filter(pl.col("kind") == OBSERVED)
    assert observed["time"].max() == utc(2026, 6, 20, 13)


def test_an_old_month_whose_last_response_is_faulty_is_asked_again(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    april = month_url(date(2026, 4, 1), date(2026, 4, 30))
    fake.bodies[april] = b"[]"
    run(fake, store, clean, now=LATER, full=True)
    del fake.bodies[april]
    fake.requests.clear()
    assert run(fake, store, clean, now=LATER + timedelta(days=1)) == Report()
    assert "observed 2026-04-01 2026-04-30" in asked(fake)


def test_a_damaged_file_of_an_old_month_is_fetched_again(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    april = next(r for r in store.receipts("openmeteo") if r.request == "2026-04")
    (store.root / april.path).write_bytes(b"damaged")
    fake.requests.clear()
    report = run(fake, store, clean, now=LATER)
    assert "observed 2026-04-01 2026-04-30" in asked(fake)
    assert value(weather(clean), "temperature_c", utc(2026, 4, 19, 12)) == 16.0
    # The damaged file stays beside the new response, until a backup brings it back.
    assert len(report.errors) == 1
    assert report.errors[0].startswith(f"raw layer: {april.path}: ")


def test_an_error_of_the_archive_stops_the_run(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    # An archive whose today lags behind refuses the end date of the current month.
    fake.now = NOW - timedelta(days=1)
    with (
        client(httpx2.MockTransport(fake.handle)) as http,
        pytest.raises(httpx2.HTTPStatusError, match="400"),
    ):
        ingest(http, store, clean, now=NOW, since=SINCE, first_run=FIRST_RUN, sleep=lambda s: None)
    assert not (clean / "openmeteo").exists()


def test_just_after_midnight_in_paris_the_day_is_still_the_utc_one(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    # 01:00 in Paris on 21 June is 23:00 UTC on 20 June: the archive stops on the 20th, and the
    # run of the 21st does not exist yet. The day before is 20 June in Paris, which just ended.
    fake.gaps.add((OBSERVED, "temperature_2m", utc(2026, 6, 20, 21)))
    report = run(fake, store, clean, now=datetime(2026, 6, 21, 1, tzinfo=PARIS))
    assert asked(fake)[3] == "observed 2026-06-01 2026-06-20"
    assert asked(fake)[-1] == "run 2026-06-20T00:00"
    assert report == Report(errors=["observed temperature_c 2026-06-20: 1 of 24 hours missing"])


def test_another_error_on_a_run_stops_the_run(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    (launch,) = runs(date(2026, 6, 12), date(2026, 6, 12))
    fake.statuses[launch.url] = 403
    with pytest.raises(httpx2.HTTPStatusError, match="403"):
        run(fake, store, clean)


def test_a_response_larger_than_the_limit_is_refused(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    fake.bodies[month_url(date(2026, 4, 1), date(2026, 4, 30))] = b" " * 1_000_001
    with pytest.raises(UnexpectedResponse, match="more than 1000000 bytes"):
        run(fake, store, clean)


def test_a_value_outside_its_limits_keeps_the_clean_file_as_it_was(
    fake: FakeOpenMeteo, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    before = weather(clean)
    fake.revision = 100.0  # above 50 °C
    report = run(fake, store, clean, now=LATER)
    assert report.invalid
    assert weather(clean).equals(before)


def test_each_new_response_and_the_whole_run_are_logged(
    fake: FakeOpenMeteo, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    run(fake, store, clean)
    assert caplog.text.count("new response kept in") == len(FIRST_ASKED)
    assert f"openmeteo: {len(FIRST_ASKED)} requests, {len(FIRST_ASKED)} new responses, " in (
        caplog.text
    )


JUNE = Request(OBSERVED, "2026-06", utc(2026, 6, 1), utc(2026, 7, 1), "https://example.test/j")
RUN = Request(FORECAST, "2026-06-01T00:00", utc(2026, 6, 1), utc(2026, 6, 3), "https://e.test/r")
URL = "https://example.test/response"


def response(hours: int = 2, **changes: object) -> bytes:
    """A response for the first hours of June, as each test changes it."""
    body: dict[str, object] = {
        "latitude": 45.729347,
        "longitude": 4.8264985,
        "generationtime_ms": 0.3,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "timezone_abbreviation": "GMT",
        "elevation": 161.0,
        "hourly_units": {"time": "iso8601", **UNITS},
        "hourly": {
            "time": [f"{utc(2026, 6, 1) + i * HOUR:%Y-%m-%dT%H:%M}" for i in range(hours)],
            **{field: [1.5] * hours for field in UNITS},
        },
    }
    return json.dumps(body | changes).encode()


def hourly(**changes: object) -> dict[str, object]:
    """The hourly part of a response of two hours, as a test changes it."""
    whole: dict[str, object] = json.loads(response())["hourly"]
    return whole | changes


def test_a_response_gives_its_values_and_leaves_its_nulls_out() -> None:
    content = response(hourly=hourly(temperature_2m=[None, 12], relative_humidity_2m=[80, 81]))
    values = parse(content, JUNE, URL)
    assert values["temperature_c"] == [(utc(2026, 6, 1, 1), 12.0)]
    assert isinstance(values["temperature_c"][0][1], float)
    assert values["wind_speed_m_s"] == [(utc(2026, 6, 1), 1.5), (utc(2026, 6, 1, 1), 1.5)]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"<html>", "not JSON"),
        (b"[]", "not an object"),
        (response(latitude=48.85), "a grid point at 48.85, 4.8264985, not near Lyon"),
        (response(longitude="4.83"), "not near Lyon"),
        (response(utc_offset_seconds=7200), "hours with an offset of 7200 s, not in UTC"),
        (response(utc_offset_seconds=False), "not in UTC"),
        (
            response(hourly_units={"time": "iso8601", **UNITS, "wind_speed_10m": "km/h"}),
            "wind_speed_10m in 'km/h', not in 'm/s'",
        ),
        (
            response(hourly_units={**UNITS, "time": "unixtime"}),
            "time in 'unixtime', not in 'iso8601'",
        ),
        (response(hourly_units=None), "time in None, not in 'iso8601'"),
        (response(hourly=None), "no hours"),
        (response(hourly=hourly(time=[])), "no hours"),
        (
            response(hourly=hourly(time=["2026-06-01T01:00", "2026-06-01T02:00"])),
            "hour 0 is '2026-06-01T01:00', not '2026-06-01T00:00'",
        ),
        (
            response(hourly=hourly(time=["2026-06-01T00:00", "2026-06-01T02:00"])),
            "hour 1 is '2026-06-01T02:00', not '2026-06-01T01:00'",
        ),
        (response(hourly=hourly(time=["2026-06-01T00:00Z", "2026-06-01T01:00Z"])), "hour 0 is"),
        (
            response(hourly=hourly(shortwave_radiation=[1.0])),
            "no list of 2 values for shortwave_radiation",
        ),
        (response(hourly=hourly(wind_speed_10m=None)), "no list of 2 values for wind_speed_10m"),
        (
            response(hourly=hourly(temperature_2m=["12", 13])),
            "unexpected temperature_2m '12' at 2026-06-01 00:00 UTC",
        ),
        (response(hourly=hourly(temperature_2m=[True, 13])), "unexpected temperature_2m True"),
        (response(hourly=hourly(temperature_2m=[float("nan"), 13])), "temperature_2m nan"),
        (response(hourly=hourly(temperature_2m=[10**400, 13])), "unexpected temperature_2m 1"),
    ],
)
def test_a_response_must_keep_its_known_shape(content: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=re.escape(message)) as error:
        parse(content, JUNE, URL)
    assert str(error.value).startswith(f"{URL}: ")


def test_a_response_cannot_hold_hours_past_its_month_or_run() -> None:
    assert len(parse(response(hours=48), RUN, URL)["temperature_c"]) == 48
    with pytest.raises(SchemaError, match="49 hours, more than the 48 of forecast 2026-06-01T00"):
        parse(response(hours=49), RUN, URL)


def test_the_fingerprint_leaves_out_generationtime_ms_and_the_order_of_the_keys() -> None:
    one = json.loads(response())
    other = dict(reversed((one | {"generationtime_ms": 131.7}).items()))
    assert fingerprint(json.dumps(one).encode()) == fingerprint(json.dumps(other).encode())
    moved = one | {"elevation": 162.0}
    assert fingerprint(json.dumps(moved).encode()) != fingerprint(json.dumps(one).encode())
    assert fingerprint(b"<html>") == b"<html>"


Row = tuple[datetime, str, float, str, datetime | None]


def frame(rows: list[Row]) -> pl.DataFrame:
    return pl.DataFrame([(*row, NOW) for row in rows], schema=SCHEMA, orient="row")


def observed_days(first: date, last: date) -> list[Row]:
    """Every hour of every variable, observed, over Paris days."""
    start, end = day_bounds(first)[0], day_bounds(last)[1]
    hours = [start + i * HOUR for i in range((end - start) // HOUR)]
    return [(moment, name, 1.0, OBSERVED, None) for name in NAMES for moment in hours]


def whole_run(day: date) -> list[Row]:
    """Every hour of a run, but for the radiations at its launch."""
    launch = datetime.combine(day, time(), UTC)
    return [
        (launch + i * HOUR, name, 1.0, FORECAST, launch)
        for name in NAMES
        for i in range(48)
        if i or name not in MEANS
    ]


def test_the_checks_count_each_variable_per_paris_day_up_to_yesterday(store: RawStore) -> None:
    # The clocks go forward on 29 March: 23 hours; they go back on 25 October: 25 hours.
    rows = [
        *observed_days(date(2026, 3, 28), date(2026, 3, 30)),
        *observed_days(date(2026, 10, 25), date(2026, 10, 25)),
    ]
    rows.remove((day_bounds(date(2026, 3, 29))[0] + 5 * HOUR, "diffuse_w_m2", 1.0, OBSERVED, None))
    rows.remove((day_bounds(date(2026, 10, 25))[0], "temperature_c", 1.0, OBSERVED, None))
    report = check(
        frame(rows),
        store,
        since=date(2026, 3, 28),
        first_run=date(2026, 11, 1),
        now=datetime(2026, 3, 31, 10, tzinfo=PARIS),
    )
    assert report == Report(errors=["observed diffuse_w_m2 2026-03-29: 1 of 23 hours missing"])
    # Just after midnight in Paris, the day before is the Paris day that has just ended, and no
    # run is awaited before the first one.
    report = check(
        frame(rows),
        store,
        since=date(2026, 10, 25),
        first_run=date(2026, 11, 1),
        now=datetime(2026, 10, 26, 0, 30, tzinfo=PARIS),
    )
    assert report == Report(errors=["observed temperature_c 2026-10-25: 1 of 25 hours missing"])


def test_the_checks_want_every_run_up_to_yesterday_whole(store: RawStore) -> None:
    rows = [*whole_run(date(2026, 6, 10)), *whole_run(date(2026, 6, 12))[1:]]
    # An hour moved off the hour is invalid, and hides no missing hour.
    moved = rows.pop()
    rows.append((moved[0] + timedelta(minutes=30), *moved[1:]))
    report = check(
        frame(rows),
        store,
        since=date(2026, 6, 13),
        first_run=date(2026, 6, 10),
        now=utc(2026, 6, 13, 10),
    )
    assert report.errors == [
        "run 2026-06-11T00:00: missing",
        "run 2026-06-12T00:00 temperature_c: 1 of 48 hours missing",
        "run 2026-06-12T00:00 wind_speed_m_s: 1 of 48 hours missing",
    ]
    assert report.invalid == [
        "2026-06-13 23:30:00 UTC: run 2026-06-12T00:00 wind_speed_m_s not on the hour"
    ]


@pytest.mark.parametrize(("minute", "warned"), [(59, False), (60, True)])
def test_the_run_of_the_day_is_awaited_until_noon_utc(
    store: RawStore, minute: int, warned: bool
) -> None:
    # 14:00 in Paris, in summer.
    now = datetime(2026, 6, 13, 13, tzinfo=PARIS) + timedelta(minutes=minute)
    rows = whole_run(date(2026, 6, 13))[1:]
    report = check(
        frame(rows), store, since=date(2026, 6, 13), first_run=date(2026, 6, 13), now=now
    )
    assert report.errors == []
    expected = "run 2026-06-13T00:00 temperature_c: 1 of 48 hours missing at 12:00 UTC"
    assert report.warnings == ([expected] if warned else [])


def quiet_check(rows: list[Row], store: RawStore) -> Report:
    """The checks on a day with nothing to count: only the rows themselves."""
    return check(
        frame(rows),
        store,
        since=date(2026, 6, 13),
        first_run=date(2026, 6, 14),
        now=utc(2026, 6, 13, 10),
    )


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (
            (utc(2026, 6, 12, 10) + timedelta(minutes=30), "temperature_c", 20.0, OBSERVED, None),
            "2026-06-12 10:30:00 UTC: observed temperature_c not on the hour",
        ),
        (
            (utc(2026, 6, 12, 10), "temperature_c", -40.5, OBSERVED, None),
            "2026-06-12 10:00 UTC: observed temperature_c -40.5, outside -40 to 50",
        ),
        (
            (utc(2026, 6, 12, 10), "temperature_c", 50.5, FORECAST, utc(2026, 6, 12)),
            "2026-06-12 10:00 UTC: run 2026-06-12T00:00 temperature_c 50.5, outside -40 to 50",
        ),
        (
            (utc(2026, 6, 12, 10), "global_w_m2", -0.1, OBSERVED, None),
            "2026-06-12 10:00 UTC: observed global_w_m2 -0.1, outside 0 to 1500",
        ),
        (
            (utc(2026, 6, 12, 10), "direct_normal_w_m2", 1500.5, OBSERVED, None),
            "2026-06-12 10:00 UTC: observed direct_normal_w_m2 1500.5, outside 0 to 1500",
        ),
        (
            (utc(2026, 6, 12, 10), "wind_speed_m_s", 75.5, OBSERVED, None),
            "2026-06-12 10:00 UTC: observed wind_speed_m_s 75.5, outside 0 to 75",
        ),
    ],
)
def test_a_value_off_the_hour_or_outside_its_limits_is_invalid(
    store: RawStore, row: Row, problem: str
) -> None:
    assert quiet_check([row], store).invalid == [problem]


def test_the_limits_themselves_are_valid(store: RawStore) -> None:
    limits = [
        ("temperature_c", -40.0),
        ("temperature_c", 50.0),
        ("global_w_m2", 0.0),
        ("direct_horizontal_w_m2", 1500.0),
        ("diffuse_w_m2", 1500.0),
        ("direct_normal_w_m2", 1500.0),
        ("wind_speed_m_s", 0.0),
        ("wind_speed_m_s", 75.0),
    ]
    rows: list[Row] = [
        (utc(2026, 6, 12, i), name, limit, OBSERVED, None) for i, (name, limit) in enumerate(limits)
    ]
    assert quiet_check(rows, store) == Report()


def test_an_hour_twice_is_invalid_in_the_observed_weather_or_in_one_run(store: RawStore) -> None:
    noon = utc(2026, 6, 12, 12)
    rows: list[Row] = [
        (noon, "temperature_c", 20.0, OBSERVED, None),
        (noon, "temperature_c", 21.0, OBSERVED, None),
        (noon, "temperature_c", 20.5, FORECAST, utc(2026, 6, 12)),
        (noon, "temperature_c", 20.5, FORECAST, utc(2026, 6, 12)),
        # The same hour in another run is no duplicate.
        (noon, "temperature_c", 20.5, FORECAST, utc(2026, 6, 11)),
    ]
    assert quiet_check(rows, store).invalid == [
        "2026-06-12 12:00 UTC: observed temperature_c 2 values",
        "2026-06-12 12:00 UTC: run 2026-06-12T00:00 temperature_c 2 values",
    ]


def test_a_raw_layer_problem_is_a_check_error(store: RawStore) -> None:
    (store.root / "openmeteo").mkdir()
    (store.root / "openmeteo" / "manifest.jsonl").write_text("")
    (store.root / "openmeteo" / "stray.json.gz").write_bytes(b"x")
    report = quiet_check([], store)
    assert report.errors == ["raw layer: openmeteo/stray.json.gz: not in the manifest"]
