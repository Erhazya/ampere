"""éCO2mix against a fake ODRÉ: four datasets served by URL, with versions set by each test."""

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
from ampere.data.days import day_bounds, first_pass, never_happened, paris_day, quarter_hours
from ampere.data.http import UnexpectedResponse, client
from ampere.data.raw import RawStore
from ampere.sources import eco2mix
from ampere.sources.eco2mix import (
    AUVERGNE_RHONE_ALPES,
    CONS_DEF,
    FRANCE,
    SCHEMA,
    TR,
    Month,
    MonthFile,
    best_version,
    check,
    complete,
    coverage_url,
    ingest,
    parse_coverage,
    parse_month,
)
from ampere.sources.shapes import SchemaError

PARIS = ZoneInfo("Europe/Paris")
API = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets"
QUARTER = timedelta(minutes=15)
HALF = timedelta(minutes=30)
NATURES = {
    "real_time": "Données temps réel",
    "consolidated": "Données consolidées",
    "definitive": "Données définitives",
}
# Each version adds its own offset to a value, so that a test sees which version won.
OFFSET = {"real_time": 0.0, "consolidated": 1.0, "definitive": 2.0}
SINCE = date(2026, 4, 1)
NOW = datetime(2026, 6, 20, 15, tzinfo=PARIS)  # a Saturday afternoon
LATER = datetime(2026, 6, 25, 15, tzinfo=PARIS)


def paris(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=PARIS).astimezone(UTC)


def consumption(moment: datetime) -> float:
    return 50_000.0 + moment.hour * 100 + moment.minute


def co2(moment: datetime) -> float:
    return 20.0 + moment.hour


def forecast(moment: datetime) -> float:
    return consumption(moment) + 250


def solar(moment: datetime) -> float:
    return float(max(0, 6 - abs(moment.hour - 12)) * 200)


def load_factor(moment: datetime) -> float:
    return solar(moment) / 40


FIELDS: dict[str, dict[str, Callable[[datetime], float]]] = {
    "national": {"consommation": consumption, "taux_co2": co2, "prevision_j1": forecast},
    "regional": {"solaire": solar, "tch_solaire": load_factor},
}


class FakeOdre:
    """ODRÉ's four éCO2mix datasets, as they behave: rows in Paris hours, in no particular order.

    Definitive data, then consolidated data up to the dates each test sets, then real time over
    its last 90 days, measured up to an hour before `now`, with RTE's forecast to the end of
    tomorrow. On the day the clocks go forward, the hour that never happened has rows at the
    instants of the next one; on the day they go back, 02:00 comes once, at its second pass.
    """

    def __init__(self) -> None:
        self.definitive_until = paris(2026, 5, 1)
        self.consolidated_until = paris(2026, 6, 1)
        # What the periods announce, when a test wants them ahead of the exports.
        self.announced_definitive_until: datetime | None = None
        # Periods that lose their consolidated data, as a faulty republication could.
        self.hide_consolidated = False
        # Real time over its whole window, even where consolidated data exist.
        self.overlap = False
        self.now = NOW
        self.lag = timedelta(hours=1)
        self.fields = {level: dict(fields) for level, fields in FIELDS.items()}
        self.gaps: set[tuple[str, datetime]] = set()
        self.bodies: dict[str, bytes] = {}
        self.requests: list[str] = []
        # When ODRÉ last updated each dataset; ten minutes before now unless a test says.
        self.processed: dict[str, datetime] = {}

    def real_time_from(self) -> datetime:
        """Real time covers 90 days that end with tomorrow, past the consolidated data."""
        today = self.now.astimezone(PARIS).date()
        window = day_bounds(today - timedelta(days=88))[0]
        return window if self.overlap else max(self.consolidated_until, window)

    def real_time_until(self, level: str) -> datetime:
        """The national forecast runs to the end of tomorrow; the region stops with today."""
        today = self.now.astimezone(PARIS).date()
        return day_bounds(today + timedelta(days=1 if level == "national" else 0))[1]

    def version(self, moment: datetime) -> str | None:
        if moment < self.definitive_until:
            return "definitive"
        if moment < self.consolidated_until:
            return "consolidated"
        return "real_time" if moment >= self.real_time_from() else None

    def periods(self, level: str, kind: str) -> list[dict[str, str]]:
        step = HALF if level == "regional" and kind == CONS_DEF else QUARTER
        definitive = self.announced_definitive_until or self.definitive_until
        if kind == TR:
            spans = [("real_time", self.real_time_from(), self.real_time_until(level))]
        else:
            spans = [
                ("definitive", paris(2025, 1, 1), definitive),
                ("consolidated", definitive, self.consolidated_until),
            ][: 1 if self.hide_consolidated else 2]
        return [
            {"nature": NATURES[name], "first": first.isoformat(), "last": (end - step).isoformat()}
            for name, first, end in spans
            if first < end
        ]

    def value(self, field: str, level: str, moment: datetime, version: str) -> float | None:
        if (field, moment) in self.gaps:
            return None
        if version == "real_time" and field != "prevision_j1" and moment >= self.now - self.lag:
            return None  # not measured yet
        if version != "real_time" and field in ("consommation", "taux_co2") and moment.minute % 30:
            return None  # measured by the half-hour
        return self.fields[level][field](moment.astimezone(PARIS)) + OFFSET[version]

    def rows(
        self, level: str, kind: str, start: datetime, end: datetime
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        day = paris_day(start)
        while day_bounds(day)[0] < end:
            for slot in range(96):
                local = datetime.combine(day, time(slot // 4, 15 * (slot % 4)))
                # Two readings of a Paris time: ODRÉ takes the later one, which gives the hour
                # that never happened the instants of the next one, and 02:00 its second pass.
                moment = max(
                    local.replace(tzinfo=PARIS, fold=fold).astimezone(UTC) for fold in (0, 1)
                )
                version = self.version(moment)
                if kind == TR and self.overlap and moment >= self.real_time_from():
                    version = "real_time"
                if not start <= moment < end or version is None:
                    continue
                if (version == "real_time") != (kind == TR):
                    continue
                if kind == TR and moment >= self.real_time_until(level):
                    continue
                if level == "regional" and kind == CONS_DEF and moment.minute % 30:
                    continue
                written = local.isoformat()
                row: dict[str, object] = {
                    "nature": NATURES[version],
                    "date": written[:10],
                    "heure": written[11:16],
                    "date_heure": moment.isoformat(),
                }
                if level == "regional":
                    row = {"code_insee_region": "84", "libelle_region": "ARA", **row}
                for field in self.fields[level]:
                    value = self.value(field, level, moment, version)
                    if never_happened(local) and field == "prevision_j1" and value is not None:
                        value -= 1_000  # the hour that never happened repeats an old forecast
                    row[field] = value
                row["eolien"] = 1234  # a field Ampère keeps in the raw layer but does not read
                rows.append(row)
            day += timedelta(days=1)
        random.Random(len(rows)).shuffle(rows)  # the exports come in no particular order
        return rows

    def catalogue(self, request: httpx2.Request) -> dict[str, object]:
        """The catalogue of ODRÉ, asked for the date each dataset was last updated."""
        assert request.url.params["select"] == "dataset_id, data_processed"
        names = re.findall(r'"([^"]+)"', request.url.params["where"])
        results = [
            {
                "dataset_id": name,
                "data_processed": self.processed.get(name, self.now - timedelta(minutes=10))
                .astimezone(UTC)
                .isoformat(),
            }
            for name in names
        ]
        return {"total_count": len(results), "results": results}

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.bodies:
            return httpx2.Response(
                200, content=self.bodies[url], headers={"content-type": "application/json"}
            )
        if request.url.path.endswith("/catalog/datasets"):
            return httpx2.Response(200, json=self.catalogue(request))
        match = re.fullmatch(
            r".*/datasets/eco2mix-(national|regional)-(tr|cons-def)/(.*)", request.url.path
        )
        if match is None:
            return httpx2.Response(404)
        level, kind, endpoint = match.groups()
        region = "code_insee_region='84'"
        if endpoint == "records":
            assert (request.url.params.get("where") == region) is (level == "regional")
            results = self.periods(level, kind)
            return httpx2.Response(200, json={"total_count": len(results), "results": results})
        if endpoint == "exports/json":
            pattern = "date_heure >= '(.+)' and date_heure < '(.+)'"
            where = re.fullmatch(
                f"{region} and {pattern}" if level == "regional" else pattern,
                request.url.params["where"],
            )
            assert where is not None
            start, end = (datetime.fromisoformat(value) for value in where.groups())
            return httpx2.Response(200, json=self.rows(level, kind, start, end))
        return httpx2.Response(404)


@pytest.fixture
def odre() -> FakeOdre:
    return FakeOdre()


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore.create(tmp_path / "raw")


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    return tmp_path / "clean"


def run(
    odre: FakeOdre,
    store: RawStore,
    clean: Path,
    now: datetime = NOW,
    full: bool = False,
    pauses: list[float] | None = None,
    since: date = SINCE,
) -> Report:
    odre.now = now
    with client(httpx2.MockTransport(odre.handle)) as http:
        sleep = pauses.append if pauses is not None else (lambda seconds: None)
        return ingest(http, store, clean, now=now, since=since, full=full, sleep=sleep)


def measures(clean: Path) -> pl.DataFrame:
    return pl.read_parquet(clean / "eco2mix" / "measures.parquet")


def at(frame: pl.DataFrame, area: str, measure: str, moment: datetime) -> dict[str, object]:
    rows = frame.filter(
        (pl.col("area") == area) & (pl.col("measure") == measure) & (pl.col("start") == moment)
    )
    assert rows.height == 1, rows
    return rows.row(0, named=True)


def on(frame: pl.DataFrame, area: str, measure: str, day: date) -> pl.DataFrame:
    """The rows of a measure over a Paris day."""
    start, end = day_bounds(day)
    return frame.filter(
        (pl.col("area") == area)
        & (pl.col("measure") == measure)
        & (pl.col("start") >= start)
        & (pl.col("start") < end)
    )


def asked(odre: FakeOdre) -> list[str]:
    """The requests, named "periods national-tr" or by dataset and month, "national-tr 2026-06";
    the request to the catalogue is left out."""
    names = []
    for request in map(httpx2.URL, odre.requests):
        if request.path.endswith("/catalog/datasets"):
            continue
        found = re.search(r"eco2mix-((?:national|regional)-(?:tr|cons-def))/(\w+)", request.path)
        assert found is not None
        if found.group(2) == "records":
            names.append(f"periods {found.group(1)}")
            continue
        start = re.search(r"date_heure >= '([^']+)'", request.params["where"])
        assert start is not None
        month = datetime.fromisoformat(start.group(1)).astimezone(PARIS)
        names.append(f"{found.group(1)} {month:%Y-%m}")
    return names


def month_file(area: eco2mix.Area, kind: str, year: int, month: int) -> MonthFile:
    following = date(year + month // 12, month % 12 + 1, 1)
    return MonthFile(area, kind, day_bounds(date(year, month, 1))[0], day_bounds(following)[0])


APRIL = month_file(FRANCE, CONS_DEF, 2026, 4)


PERIODS = {
    "definitive": (paris(2025, 1, 1), paris(2026, 5, 1) - HALF),
    "consolidated": (paris(2026, 5, 1), paris(2026, 6, 1) - HALF),
}


@pytest.mark.parametrize(
    ("periods", "month", "version"),
    [
        (PERIODS, (paris(2026, 4, 1), paris(2026, 5, 1)), "definitive"),
        (PERIODS, (paris(2026, 5, 1), paris(2026, 6, 1)), "consolidated"),
        (PERIODS, (paris(2026, 6, 1), paris(2026, 7, 1)), "real_time"),
        (PERIODS, (paris(2024, 12, 1), paris(2025, 1, 1)), "real_time"),  # before the periods
        # A month across the definitive and the consolidated data is covered whole.
        (
            {
                "definitive": (paris(2025, 1, 1), paris(2026, 4, 15) - HALF),
                "consolidated": (paris(2026, 4, 15), paris(2026, 6, 1) - HALF),
            },
            (paris(2026, 4, 1), paris(2026, 5, 1)),
            "consolidated",
        ),
        # Consolidated data without their last half-hour: the month is not covered whole.
        (
            {"consolidated": (paris(2026, 5, 1), paris(2026, 6, 1) - 2 * HALF)},
            (paris(2026, 5, 1), paris(2026, 6, 1)),
            "real_time",
        ),
    ],
    ids=["definitive", "consolidated", "after", "before", "across", "short"],
)
def test_a_month_takes_the_most_final_version_that_covers_it_whole(
    periods: dict[str, tuple[datetime, datetime]], month: tuple[datetime, datetime], version: str
) -> None:
    assert best_version(periods, *month) == version


def test_the_urls_name_the_dataset_the_region_and_the_month() -> None:
    assert APRIL.url == (
        f"{API}/eco2mix-national-cons-def/exports/json?where=date_heure+%3E%3D+%27"
        "2026-03-31T22%3A00%3A00%2B00%3A00%27+and+date_heure+%3C+%27"
        "2026-04-30T22%3A00%3A00%2B00%3A00%27"
    )
    regional = month_file(AUVERGNE_RHONE_ALPES, TR, 2026, 4)
    assert "where=code_insee_region%3D%2784%27+and+date_heure" in regional.url
    assert coverage_url(FRANCE, TR).startswith(f"{API}/eco2mix-national-tr/records?select=")
    assert "code_insee_region%3D%2784%27" in coverage_url(AUVERGNE_RHONE_ALPES, CONS_DEF)


def test_the_first_run_fetches_each_month_from_the_dataset_that_holds_it(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    report = run(odre, store, clean)
    assert report.invalid == [] and report.errors == [] and report.warnings == []
    assert asked(odre) == [
        "periods national-cons-def",
        "periods national-tr",
        "national-cons-def 2026-04",
        "national-cons-def 2026-05",
        "national-tr 2026-06",
        "periods regional-cons-def",
        "periods regional-tr",
        "regional-cons-def 2026-04",
        "regional-cons-def 2026-05",
        "regional-tr 2026-06",
    ]
    frame = measures(clean)
    assert dict(frame.schema) == dict(SCHEMA)
    counts = dict(frame.group_by("measure").len().rows())
    days = 80  # 1 April to 19 June; real time goes on to 14:00 on the 20th
    assert counts["consumption_mw"] == counts["co2_g_per_kwh"] == days * 96 + 14 * 4
    assert counts["rte_forecast_mw"] == (days + 2) * 96  # up to tomorrow evening
    assert counts["solar_mw"] == counts["solar_load_factor_pct"] == days * 96 + 14 * 4
    assert frame.equals(frame.sort("area", "measure", "start"))


def test_a_half_hour_value_covers_its_two_quarter_hours(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    frame = measures(clean)
    for moment in (paris(2026, 4, 10, 7), paris(2026, 4, 10, 7, 15)):
        row = at(frame, "FR", "consumption_mw", moment)
        assert row["value"] == 50_000 + 700 + 2  # the 07:00 value, definitive
        assert (row["step_minutes"], row["version"]) == (30, "definitive")
    # RTE's forecast has a value per quarter-hour in every version.
    row = at(frame, "FR", "rte_forecast_mw", paris(2026, 4, 10, 7, 15))
    assert (row["value"], row["step_minutes"]) == (50_000 + 715 + 250 + 2, 15)
    row = at(frame, "ARA", "solar_load_factor_pct", paris(2026, 5, 10, 12, 45))
    assert (row["value"], row["step_minutes"], row["version"]) == (
        1200 / 40 + 1,
        30,
        "consolidated",
    )
    row = at(frame, "FR", "consumption_mw", paris(2026, 6, 10, 7, 15))
    assert (row["value"], row["step_minutes"], row["version"]) == (50_715.0, 15, "real_time")


def test_a_later_run_asks_only_for_the_months_that_can_still_change(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    files = sorted(store.root.rglob("*.gz"))
    odre.requests.clear()
    run(odre, store, clean, now=LATER)
    assert asked(odre) == [
        "periods national-cons-def",
        "periods national-tr",
        "national-tr 2026-06",
        "periods regional-cons-def",
        "periods regional-tr",
        "regional-tr 2026-06",
    ]
    # June and the periods of real time have changed, one new response each per zone, and so
    # have the dates of the catalogue.
    assert len(sorted(store.root.rglob("*.gz"))) == len(files) + 5


@pytest.mark.parametrize(
    ("now", "may_asked"),
    [
        (datetime(2026, 6, 14, 23, 59, tzinfo=PARIS), True),
        (datetime(2026, 6, 15, 0, 0, tzinfo=PARIS), False),
    ],
    ids=["a minute before", "14 days after its end"],
)
def test_a_month_can_change_until_14_days_after_its_end(
    odre: FakeOdre, store: RawStore, clean: Path, now: datetime, may_asked: bool
) -> None:
    run(odre, store, clean, now=datetime(2026, 6, 10, 15, tzinfo=PARIS))
    odre.requests.clear()
    run(odre, store, clean, now=now)
    assert ("national-cons-def 2026-05" in asked(odre)) is may_asked


def test_a_real_time_month_past_its_14_days_is_not_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.definitive_until = odre.consolidated_until = paris(2026, 5, 1)  # May in real time
    run(odre, store, clean)
    odre.requests.clear()
    run(odre, store, clean, now=LATER)
    assert "national-tr 2026-05" not in asked(odre)
    assert "national-tr 2026-06" in asked(odre)


def test_a_real_time_month_missing_a_quarter_hour_is_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.definitive_until = odre.consolidated_until = paris(2026, 5, 1)
    odre.gaps.add(("consommation", paris(2026, 5, 10, 7, 15)))  # between two half-hours
    report = run(odre, store, clean)
    assert report.errors == ["FR consumption_mw 2026-05-10: 1 of 96 quarter-hours missing"]
    odre.gaps.clear()  # RTE fills the gap
    odre.requests.clear()
    report = run(odre, store, clean, now=LATER)
    assert "national-tr 2026-05" in asked(odre)
    assert report.errors == []


def test_a_real_time_month_that_has_left_the_window_is_kept_even_with_full(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.definitive_until = odre.consolidated_until = paris(2026, 4, 1)  # April in real time
    assert run(odre, store, clean).errors == []
    # On 10 July, real time starts on 13 April: asked again, April would come back cut.
    odre.requests.clear()
    report = run(odre, store, clean, now=datetime(2026, 7, 10, 15, tzinfo=PARIS), full=True)
    assert "national-tr 2026-04" not in asked(odre)
    assert "national-tr 2026-05" in asked(odre)
    assert report.errors == []
    assert on(measures(clean), "FR", "consumption_mw", date(2026, 4, 1)).height == 96


def test_a_month_that_turns_consolidated_comes_from_the_consolidated_dataset(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    odre.consolidated_until = paris(2026, 7, 1)  # RTE publishes June, consolidated
    odre.requests.clear()
    report = run(odre, store, clean, now=datetime(2026, 7, 20, 15, tzinfo=PARIS))
    assert report.invalid == [] and report.errors == []
    assert "national-cons-def 2026-06" in asked(odre)
    assert "regional-cons-def 2026-06" in asked(odre)
    # The real-time response of June stays in the raw layer, unread.
    row = at(measures(clean), "FR", "consumption_mw", paris(2026, 6, 10, 7, 15))
    assert (row["value"], row["step_minutes"], row["version"]) == (50_701.0, 30, "consolidated")


def test_a_damaged_real_time_file_of_a_month_now_consolidated_blocks_no_run(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    june = next(r for r in store.receipts("eco2mix") if r.dataset == "national-tr")
    (store.root / june.path).write_bytes(b"damaged")
    odre.consolidated_until = paris(2026, 7, 1)
    report = run(odre, store, clean, now=datetime(2026, 7, 20, 15, tzinfo=PARIS))
    # The raw layer tells of the damage, but June comes from its consolidated data.
    assert [error for error in report.errors if error.startswith("raw layer")]
    assert at(measures(clean), "FR", "co2_g_per_kwh", paris(2026, 6, 10, 7))["version"] == (
        "consolidated"
    )


def test_an_unreadable_real_time_response_of_a_month_now_consolidated_stops_nothing(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    june = month_file(FRANCE, TR, 2026, 6)
    odre.bodies[june.url] = b'{"error": "try later"}'
    assert f"{june.url}: not a list of rows" in run(odre, store, clean).errors
    del odre.bodies[june.url]
    odre.consolidated_until = paris(2026, 7, 1)
    report = run(odre, store, clean, now=datetime(2026, 7, 20, 15, tzinfo=PARIS))
    assert report.errors == [] and report.invalid == []


def test_a_faulty_month_is_an_error_and_the_run_goes_on(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    june = month_file(FRANCE, TR, 2026, 6)
    odre.bodies[june.url] = b'{"error": "try later"}'
    report = run(odre, store, clean)
    assert f"{june.url}: not a list of rows" in report.errors
    assert "FR consumption_mw 2026-06-10: 96 of 96 quarter-hours missing" in report.errors
    assert "regional-tr 2026-06" in asked(odre)
    frame = measures(clean)
    assert on(frame, "FR", "consumption_mw", date(2026, 5, 10)).height == 96
    assert on(frame, "ARA", "solar_mw", date(2026, 6, 10)).height == 96


def test_a_faulty_month_keeps_the_response_kept_for_it(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    june = month_file(FRANCE, TR, 2026, 6)
    odre.bodies[june.url] = b'{"error": "try later"}'
    report = run(odre, store, clean, now=LATER)
    assert f"{june.url}: not a list of rows" in report.errors
    # June comes from the response kept on the 20th: whole up to then, missing after.
    frame = measures(clean)
    assert on(frame, "FR", "consumption_mw", date(2026, 6, 10)).height == 96
    assert "FR consumption_mw 2026-06-22: 96 of 96 quarter-hours missing" in report.errors


def test_a_faulty_response_of_the_periods_stops_the_run(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.bodies[coverage_url(FRANCE, CONS_DEF)] = b"[]"
    with pytest.raises(SchemaError, match="no list of results"):
        run(odre, store, clean)


def test_an_empty_consolidated_export_falls_back_on_the_real_time_kept(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    odre.consolidated_until = paris(2026, 7, 1)  # June announced consolidated...
    odre.bodies[month_file(FRANCE, CONS_DEF, 2026, 6).url] = b"[]"  # ...but not exported yet
    report = run(odre, store, clean, now=datetime(2026, 7, 20, 15, tzinfo=PARIS))
    assert report.warnings == [
        "FR 2026-06: ODRÉ announces consolidated data, but its export is empty",
        "FR 2026-06: the kept national-tr response is used: it covers more quarter-hours than "
        "national-cons-def",
    ]
    row = at(measures(clean), "FR", "consumption_mw", paris(2026, 6, 10, 7, 15))
    assert (row["value"], row["version"]) == (50_715.0, "real_time")


def test_with_equal_coverage_the_response_of_the_chosen_dataset_stays(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean, now=datetime(2026, 7, 5, 15, tzinfo=PARIS))  # June whole in real time
    odre.consolidated_until = paris(2026, 7, 1)
    report = run(odre, store, clean, now=datetime(2026, 7, 20, 15, tzinfo=PARIS))
    assert not [warning for warning in report.warnings if "is used" in warning]
    row = at(measures(clean), "FR", "consumption_mw", paris(2026, 6, 10, 7, 15))
    assert row["version"] == "consolidated"


def test_with_equal_coverage_the_most_final_version_wins(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.overlap = True  # real time covers its whole window, May included
    run(odre, store, clean)
    odre.hide_consolidated = True  # May is now chosen from real time, whole
    report = run(odre, store, clean, now=LATER)
    assert "national-tr 2026-05" in asked(odre)
    assert (
        "FR 2026-05: the kept national-cons-def response is used: it is more final than national-tr"
        in report.warnings
    )
    row = at(measures(clean), "FR", "co2_g_per_kwh", paris(2026, 5, 10, 7))
    assert row["version"] == "consolidated"


def test_periods_that_lose_the_consolidated_data_keep_the_consolidated_months(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    odre.hide_consolidated = True
    report = run(odre, store, clean, now=LATER)
    assert "national-tr 2026-05" in asked(odre)  # asked, and empty
    assert (
        "FR 2026-05: the kept national-cons-def response is used: it covers more quarter-hours "
        "than national-tr" in report.warnings
    )
    assert report.errors == []
    row = at(measures(clean), "FR", "co2_g_per_kwh", paris(2026, 5, 10, 7))
    assert (row["value"], row["version"]) == (27 + 1, "consolidated")


def test_a_real_time_month_out_of_the_window_is_asked_when_nothing_is_kept(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.definitive_until = odre.consolidated_until = paris(2026, 4, 1)
    # A first run on 10 July: real time starts on 13 April, and April comes in part.
    report = run(odre, store, clean, now=datetime(2026, 7, 10, 15, tzinfo=PARIS))
    assert "national-tr 2026-04" in asked(odre)
    assert "FR consumption_mw 2026-04-12: 96 of 96 quarter-hours missing" in report.errors
    assert "FR consumption_mw 2026-04-13: 96 of 96 quarter-hours missing" not in report.errors


def test_a_consolidated_month_that_turns_definitive_is_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    odre.definitive_until = paris(2026, 6, 1)  # May becomes definitive
    odre.requests.clear()
    report = run(odre, store, clean, now=LATER)
    assert "national-cons-def 2026-05" in asked(odre)
    assert "national-cons-def 2026-04" not in asked(odre)
    row = at(measures(clean), "FR", "co2_g_per_kwh", paris(2026, 5, 10, 7))
    assert (row["value"], row["version"]) == (27 + 2, "definitive")
    assert report.warnings == []


def test_a_version_announced_but_not_exported_is_a_warning(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    odre.announced_definitive_until = paris(2026, 6, 1)  # the export of May is still consolidated
    report = run(odre, store, clean, now=LATER)
    assert report.warnings == [
        "FR 2026-05: ODRÉ announces definitive data, but its export is consolidated",
        "ARA 2026-05: ODRÉ announces definitive data, but its export is consolidated",
    ]


def test_full_asks_for_every_month_again(odre: FakeOdre, store: RawStore, clean: Path) -> None:
    run(odre, store, clean)
    odre.requests.clear()
    run(odre, store, clean, now=LATER, full=True)
    assert len(odre.requests) == 11


def test_the_requests_are_spaced_out(odre: FakeOdre, store: RawStore, clean: Path) -> None:
    pauses: list[float] = []
    run(odre, store, clean, pauses=pauses)
    assert pauses == [0.5] * 10


def test_on_the_last_day_of_a_month_the_forecast_of_tomorrow_is_fetched(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    report = run(odre, store, clean, now=datetime(2026, 6, 30, 15, tzinfo=PARIS))
    assert "national-tr 2026-07" in asked(odre)
    assert on(measures(clean), "FR", "rte_forecast_mw", date(2026, 7, 1)).height == 96
    assert report.warnings == []


@pytest.mark.parametrize(("hour", "warned"), [(14, True), (13, False)])
def test_a_late_forecast_for_tomorrow_is_a_warning_in_the_afternoon(
    odre: FakeOdre, store: RawStore, clean: Path, hour: int, warned: bool
) -> None:
    tomorrow = day_bounds(date(2026, 6, 21))[0]
    odre.gaps.update(("prevision_j1", tomorrow + i * QUARTER) for i in range(96))
    report = run(odre, store, clean, now=datetime(2026, 6, 20, hour, tzinfo=PARIS))
    expected = ["2026-06-21: 0 of 96 quarter-hours of RTE's forecast published so far"]
    assert report.warnings == (expected if warned else [])


def test_real_time_that_stopped_is_a_warning_even_with_the_forecast_of_tomorrow(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.lag = timedelta(hours=5)
    report = run(odre, store, clean)
    assert report.warnings == [
        "real time: the last FR consumption_mw ends at 2026-06-20 08:00 UTC",
        "real time: the last ARA solar_mw ends at 2026-06-20 08:00 UTC",
    ]


def test_a_month_kept_with_missing_values_is_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.gaps.add(("taux_co2", paris(2026, 4, 10, 7)))
    report = run(odre, store, clean)
    assert report.errors == ["FR co2_g_per_kwh 2026-04-10: 2 of 96 quarter-hours missing"]
    odre.gaps.clear()  # RTE fills the gap
    odre.requests.clear()
    report = run(odre, store, clean, now=LATER)
    assert "national-cons-def 2026-04" in asked(odre)
    assert report.errors == []


def test_missing_values_still_replace_the_clean_file(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    odre.gaps.add(("consommation", paris(2026, 6, 10, 7)))
    odre.fields["national"]["taux_co2"] = lambda moment: 45.0
    report = run(odre, store, clean, now=datetime(2026, 6, 20, 16, tzinfo=PARIS))
    assert report.errors == ["FR consumption_mw 2026-06-10: 1 of 96 quarter-hours missing"]
    # The new file holds the new values, and the gap stays visible in it.
    assert at(measures(clean), "FR", "co2_g_per_kwh", paris(2026, 6, 10, 7))["value"] == 45.0
    assert on(measures(clean), "FR", "consumption_mw", date(2026, 6, 10)).height == 95


def test_a_damaged_file_of_an_old_month_is_written_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    april = next(r for r in store.receipts("eco2mix") if r.dataset == "regional-cons-def")
    (store.root / april.path).write_bytes(b"damaged")
    odre.requests.clear()
    report = run(odre, store, clean, now=LATER)
    assert "regional-cons-def 2026-04" in asked(odre)
    assert store.read(april)
    assert not [error for error in report.errors if error.startswith("raw layer")]


def test_an_old_month_received_with_an_unknown_shape_is_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.bodies[APRIL.url] = b'{"error": "try later"}'
    assert f"{APRIL.url}: not a list of rows" in run(odre, store, clean).errors
    del odre.bodies[APRIL.url]
    report = run(odre, store, clean, now=LATER)
    assert report.errors == [] and report.invalid == []


def test_the_clock_changes_as_odre_gives_them(store: RawStore, clean: Path) -> None:
    odre = FakeOdre()
    odre.definitive_until = odre.consolidated_until = paris(2026, 11, 1)
    report = run(
        odre, store, clean, now=datetime(2026, 11, 20, 15, tzinfo=PARIS), since=date(2026, 3, 1)
    )
    assert report.invalid == [] and report.errors == [] and report.warnings == []
    frame = measures(clean)
    # 29 March has 92 quarter-hours, without the forecast of the hour that never happened.
    spring = on(frame, "FR", "rte_forecast_mw", date(2026, 3, 29))
    assert spring.height == 92
    assert at(frame, "FR", "rte_forecast_mw", paris(2026, 3, 29, 3))["value"] == 50_300 + 250 + 2
    # 25 October has 100, of which ODRÉ gives 96: the first pass of 02:00, from 00:00 to 00:45
    # UTC, stays empty; the second, from 01:00, is there.
    autumn = on(frame, "ARA", "solar_mw", date(2026, 10, 25))
    assert autumn.height == 96
    first = datetime(2026, 10, 25, tzinfo=UTC)
    assert autumn.filter(pl.col("start").is_between(first, first + 3 * QUARTER)).is_empty()
    second = autumn.filter(pl.col("start").is_between(first + 4 * QUARTER, first + 7 * QUARTER))
    assert second.height == 4


def test_each_new_response_and_the_whole_run_are_logged(
    odre: FakeOdre, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ampere")
    run(odre, store, clean)
    kept = [record for record in caplog.records if "new response kept" in record.getMessage()]
    assert len(kept) == 11
    assert re.search(r"eco2mix: 11 requests, 11 new responses, \d+ values", caplog.text)
    caplog.clear()
    run(odre, store, clean)
    assert re.search(r"eco2mix: 7 requests, 0 new responses, \d+ values", caplog.text)


def test_a_response_larger_than_the_limit_is_refused(
    odre: FakeOdre, store: RawStore, clean: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(eco2mix, "MAX_BYTES", 1_000)
    with pytest.raises(UnexpectedResponse, match="more than 1000 bytes"):
        run(odre, store, clean)


def test_a_value_outside_its_limits_keeps_the_clean_file_as_it_was(
    odre: FakeOdre, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run(odre, store, clean)
    before = (clean / "eco2mix" / "measures.parquet").read_bytes()
    odre.fields["national"]["taux_co2"] = lambda moment: 600.0
    report = run(odre, store, clean, now=LATER)
    assert report.invalid[0] == "2026-05-31 22:00 UTC: FR co2_g_per_kwh 600.0, outside 0 to 500"
    assert (clean / "eco2mix" / "measures.parquet").read_bytes() == before
    assert "measures.parquet kept as it was" in caplog.text


def month_body(rows: list[object]) -> bytes:
    return json.dumps(rows).encode()


ROW: dict[str, object] = {
    "nature": "Données définitives",
    "date": "2026-04-10",
    "heure": "07:00",
    "date_heure": "2026-04-10T05:00:00+00:00",
    "consommation": 50_700,
    "taux_co2": 27,
    "prevision_j1": 50_950,
}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not json", "not JSON"),
        (b"[" * 100_000 + b"]" * 100_000, "not JSON"),
        (b'{"results": []}', "not a list of rows"),
        (month_body([["2026-04-10T05:00:00+00:00", 50_700]]), "not an object"),
        (month_body([{**ROW, "date_heure": "2026-04-10T05:00:00"}]), "date_heure"),
        (month_body([{**ROW, "date_heure": 1775797200000}]), "date_heure"),
        (month_body([{**ROW, "date_heure": "0001-01-01T00:00:00+01:00"}]), "date_heure"),
        (month_body([{**ROW, "date_heure": "2026-05-10T05:00:00+00:00"}]), "outside the month"),
        (month_body([{**ROW, "nature": "Données provisoires"}]), "nature"),
        (month_body([{**ROW, "nature": "Données temps réel"}]), "nature"),
        (month_body([{**ROW, "consommation": "50700"}]), "consommation"),
        (month_body([{**ROW, "consommation": 10**400}]), "consommation"),
        (month_body([{**ROW, "taux_co2": True}]), "taux_co2"),
        (month_body([ROW, ROW]), "two rows"),
        (month_body([{**ROW, "date_heure": "2026-04-10T05:07:00+00:00"}]), "off the quarter-hour"),
        # A consolidated or definitive consumption between two half-hours: the step has changed.
        (
            month_body([{**ROW, "date_heure": "2026-04-10T05:15:00+00:00", "heure": "07:15"}]),
            "between two half-hours",
        ),
    ],
)
def test_a_month_must_keep_its_known_shape(body: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=re.escape(APRIL.url)) as error:
        parse_month(body, APRIL)
    assert message in str(error.value)


def test_a_real_time_month_holds_only_real_time_rows() -> None:
    june = month_file(FRANCE, TR, 2026, 6)
    row = {**ROW, "date": "2026-06-10", "date_heure": "2026-06-10T05:00:00+00:00"}
    with pytest.raises(SchemaError, match="nature"):
        parse_month(month_body([row]), june)


def test_a_regional_month_holds_only_its_region() -> None:
    row = {"code_insee_region": "11", "nature": "Données temps réel"}
    body = month_body([{**row, "date_heure": "2026-06-10T05:00:00+00:00", "solaire": 800}])
    with pytest.raises(SchemaError, match="region '11'"):
        parse_month(body, month_file(AUVERGNE_RHONE_ALPES, TR, 2026, 6))


MARCH_2024 = month_file(FRANCE, CONS_DEF, 2024, 3)
REAL = {**ROW, "date": "2024-03-31", "heure": "03:00", "date_heure": "2024-03-31T01:00:00+00:00"}
GHOST = {**REAL, "heure": "02:00", "prevision_j1": 46_423}


@pytest.mark.parametrize(
    "rows",
    [
        [{**REAL, "prevision_j1": 46_646}, GHOST],
        [GHOST, {**REAL, "prevision_j1": 46_646}],
    ],
    ids=["after", "before"],
)
def test_the_hour_that_never_happened_is_left_out(rows: list[object]) -> None:
    # 31 March 2024: ODRÉ gives 02:00 to 02:45, which never happened, the instants of 03:00 and on.
    month = parse_month(month_body(rows), MARCH_2024)
    assert month.values["rte_forecast_mw"] == [(paris(2024, 3, 31, 3), 46_646.0, "definitive")]


def test_the_hour_that_never_happened_is_left_out_even_alone() -> None:
    twin = {**GHOST, "heure": "02:15"}
    month = parse_month(month_body([GHOST, twin]), MARCH_2024)
    assert month.values == {"consumption_mw": [], "co2_g_per_kwh": [], "rte_forecast_mw": []}


def periods_body(*periods: tuple[str, object, object]) -> bytes:
    rows = [{"nature": nature, "first": first, "last": last} for nature, first, last in periods]
    return json.dumps({"total_count": len(rows), "results": rows}).encode()


START = paris(2023, 7, 1)
DEFINITIVE = ("Données définitives", "2012-01-01T00:00:00+00:00", "2024-12-31T22:45:00+00:00")
CONSOLIDATED = ("Données consolidées", "2024-12-31T23:00:00+00:00", "2026-06-30T21:45:00+00:00")
REAL_TIME = ("Données temps réel", "2026-06-30T22:00:00+00:00", "2026-09-28T21:45:00+00:00")


def test_the_periods_of_the_versions_are_read_as_odre_gives_them() -> None:
    periods = parse_coverage(periods_body(DEFINITIVE, CONSOLIDATED), "u", CONS_DEF, START)
    assert periods == {
        "definitive": (
            datetime(2012, 1, 1, tzinfo=UTC),
            datetime(2024, 12, 31, 22, 45, tzinfo=UTC),
        ),
        "consolidated": (
            datetime(2024, 12, 31, 23, tzinfo=UTC),
            datetime(2026, 6, 30, 21, 45, tzinfo=UTC),
        ),
    }
    assert parse_coverage(periods_body(DEFINITIVE), "u", CONS_DEF, START).keys() == {"definitive"}
    assert parse_coverage(periods_body(REAL_TIME), "u", TR, START).keys() == {"real_time"}


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (b"[]", CONS_DEF),
        (b'{"results": {}}', CONS_DEF),
        (periods_body(), CONS_DEF),  # nothing consolidated nor definitive
        (periods_body(CONSOLIDATED), CONS_DEF),  # no definitive data
        (
            periods_body(
                ("Données définitives", "2024-01-01T00:00:00+00:00", "2024-12-31T22:45:00+00:00")
            ),
            CONS_DEF,
        ),
        (
            periods_body(
                DEFINITIVE,
                ("Données consolidées", "2025-02-01T00:00:00+00:00", "2026-06-30T21:45:00+00:00"),
            ),
            CONS_DEF,
        ),
        (periods_body(DEFINITIVE, REAL_TIME), CONS_DEF),
        (periods_body(("Données consolidées", "2026-06-01", None)), CONS_DEF),
        (periods_body(), TR),
        (periods_body(DEFINITIVE), TR),
    ],
    ids=[
        "a list",
        "no list",
        "empty",
        "no definitive",
        "definitive too late",
        "a hole",
        "real time",
        "a bad date",
        "no real time",
        "not real time",
    ],
)
def test_the_periods_must_reach_back_to_the_start_and_follow_each_other(
    body: bytes, kind: str
) -> None:
    with pytest.raises(SchemaError, match=re.escape("https://example.test/periods")):
        parse_coverage(body, "https://example.test/periods", kind, START)


def frame(rows: list[tuple[datetime, str, str, float, int, str]]) -> pl.DataFrame:
    received = datetime(2026, 6, 20, tzinfo=UTC)
    return pl.DataFrame(
        [
            (start, area, name, value, step, version, received)
            for start, area, name, value, step, version in rows
        ],
        schema=SCHEMA,
        orient="row",
    )


def whole_days(first: date, last: date) -> list[tuple[datetime, str, str, float, int, str]]:
    """Every quarter-hour of every measure, from first to last, first pass of 02:00 included."""
    rows = []
    day = first
    while day <= last:
        start = day_bounds(day)[0]
        for i in range(quarter_hours(day)):
            moment = start + i * QUARTER
            rows += [
                (moment, "FR", "consumption_mw", 50_000.0, 30, "definitive"),
                (moment, "FR", "co2_g_per_kwh", 20.0, 30, "definitive"),
                (moment, "FR", "rte_forecast_mw", 50_000.0, 15, "definitive"),
                (moment, "ARA", "solar_mw", 0.0, 30, "definitive"),
                (moment, "ARA", "solar_load_factor_pct", 0.0, 30, "definitive"),
            ]
        day += timedelta(days=1)
    return rows


def real_time(moment: datetime) -> list[tuple[datetime, str, str, float, int, str]]:
    """The last real-time values of both zones."""
    return [
        (moment, "FR", "consumption_mw", 50_000.0, 15, "real_time"),
        (moment, "ARA", "solar_mw", 0.0, 15, "real_time"),
    ]


OCTOBER = [
    row for row in whole_days(date(2026, 10, 24), date(2026, 10, 25)) if not first_pass(row[0])
]


def test_the_checks_count_each_measure_until_yesterday_with_the_clock_changes(
    store: RawStore,
) -> None:
    # 25 October 2026 has 100 quarter-hours, but ODRÉ never gives the first pass of 02:00 to 02:59:
    # 96 are expected. The 26th is today, not checked yet.
    rows = [r for r in OCTOBER if not (r[2] == "solar_mw" and r[0] == paris(2026, 10, 25, 12))]
    now = datetime(2026, 10, 26, 3, tzinfo=PARIS)
    report = check(frame(rows), store, since=date(2026, 10, 24), now=now)
    assert report.errors == ["ARA solar_mw 2026-10-25: 1 of 96 quarter-hours missing"]


def test_the_day_before_is_checked_once_real_time_has_come(store: RawStore) -> None:
    rows = [r for r in OCTOBER if not (r[2] == "solar_mw" and r[0] == paris(2026, 10, 25, 23, 45))]
    before = check(
        frame(rows),
        store,
        since=date(2026, 10, 24),
        now=datetime(2026, 10, 26, 2, 59, tzinfo=PARIS),
    )
    after = check(
        frame(rows), store, since=date(2026, 10, 24), now=datetime(2026, 10, 26, 3, tzinfo=PARIS)
    )
    assert before.errors == []
    assert after.errors == ["ARA solar_mw 2026-10-25: 1 of 96 quarter-hours missing"]


def test_a_first_pass_that_appears_hides_no_gap_and_is_a_warning(store: RawStore) -> None:
    rows = whole_days(date(2026, 10, 24), date(2026, 10, 25))  # with the first pass of 02:00
    rows = [r for r in rows if not (r[2] == "solar_mw" and r[0] == paris(2026, 10, 25, 12))]
    now = datetime(2026, 10, 26, 3, tzinfo=PARIS)
    report = check(
        frame([*rows, *real_time(paris(2026, 10, 26, 1, 45))]),
        store,
        since=date(2026, 10, 24),
        now=now,
    )
    assert report.errors == ["ARA solar_mw 2026-10-25: 1 of 96 quarter-hours missing"]
    assert report.warnings == ["2026-10-25: ODRÉ now gives the first pass of 02:00, not counted"]


def test_an_autumn_month_is_whole_without_the_first_pass_of_the_hour_lived_twice() -> None:
    october = month_file(FRANCE, CONS_DEF, 2026, 10)

    def values(step: timedelta) -> list[tuple[datetime, float, str]]:
        instants = (october.start + i * step for i in range((october.end - october.start) // step))
        return [(moment, 50_000.0, "definitive") for moment in instants if not first_pass(moment)]

    month = Month(
        {
            "consumption_mw": values(HALF),
            "co2_g_per_kwh": values(HALF),
            "rte_forecast_mw": values(QUARTER),
        },
        {"definitive"},
    )
    assert complete(month, october)
    month.values["co2_g_per_kwh"].pop(100)
    assert not complete(month, october)


@pytest.mark.parametrize(
    ("row", "invalid"),
    [
        (
            (paris(2026, 10, 25, 9), "FR", "consumption_mw", 9_999.0, 30, "definitive"),
            "outside 10000 to 150000",
        ),
        (
            (paris(2026, 10, 25, 9), "FR", "co2_g_per_kwh", -1.0, 30, "definitive"),
            "outside 0 to 500",
        ),
        (
            (paris(2026, 10, 25, 9), "ARA", "solar_load_factor_pct", 150.5, 30, "definitive"),
            "outside 0 to 150",
        ),
        (
            (paris(2026, 10, 26, 12), "FR", "consumption_mw", 50_000.0, 15, "real_time"),
            "after the run",
        ),
        (
            (paris(2026, 10, 28, 0), "FR", "rte_forecast_mw", 50_000.0, 15, "real_time"),
            "after tomorrow",
        ),
        (
            (paris(2026, 10, 25, 9, 7), "FR", "co2_g_per_kwh", 20.0, 30, "definitive"),
            "not on a quarter-hour",
        ),
    ],
)
def test_a_value_outside_its_limits_or_its_grid_or_after_its_time_is_invalid(
    store: RawStore, row: tuple[datetime, str, str, float, int, str], invalid: str
) -> None:
    rows = [r for r in OCTOBER if r[:3] != row[:3]]
    now = datetime(2026, 10, 26, 11, tzinfo=PARIS)
    report = check(frame([*rows, row]), store, since=date(2026, 10, 24), now=now)
    assert len(report.invalid) == 1 and invalid in report.invalid[0]


def test_a_quarter_hour_twice_is_an_invalid_row(store: RawStore) -> None:
    twice = (paris(2026, 10, 25, 9), "ARA", "solar_mw", 0.0, 30, "definitive")
    now = datetime(2026, 10, 26, 11, tzinfo=PARIS)
    report = check(frame([*OCTOBER, twice]), store, since=date(2026, 10, 24), now=now)
    # 09:00 in Paris, winter time since the night: 08:00 UTC.
    assert report.invalid == ["2026-10-25 08:00 UTC: ARA solar_mw 2 values"]


def test_the_forecast_of_tomorrow_and_the_limits_themselves_are_valid(store: RawStore) -> None:
    extra = [
        (paris(2026, 10, 27, 23, 45), "FR", "rte_forecast_mw", 10_000.0, 15, "real_time"),
        (paris(2026, 10, 26, 10, 45), "FR", "consumption_mw", 150_000.0, 15, "real_time"),
        (paris(2026, 10, 26, 10, 45), "ARA", "solar_mw", 0.0, 15, "real_time"),
        (paris(2026, 10, 26, 10, 45), "ARA", "solar_load_factor_pct", 150.0, 15, "real_time"),
    ]
    report = check(
        frame([*OCTOBER, *extra]),
        store,
        since=date(2026, 10, 24),
        now=datetime(2026, 10, 26, 11, tzinfo=PARIS),
    )
    assert report.invalid == [] and report.errors == [] and report.warnings == []


@pytest.mark.parametrize(("hours", "warned"), [(3, False), (4, True)])
def test_real_time_more_than_3_hours_old_is_a_warning(
    store: RawStore, hours: int, warned: bool
) -> None:
    last = paris(2026, 10, 26, 10, 45)  # the quarter-hour ends at 11:00
    now = datetime(2026, 10, 26, 11 + hours, tzinfo=PARIS)
    report = check(frame([*OCTOBER, *real_time(last)]), store, since=date(2026, 10, 24), now=now)
    stale = [warning for warning in report.warnings if warning.startswith("real time")]
    assert len(stale) == (2 if warned else 0)


def test_no_real_time_at_all_is_a_warning(store: RawStore) -> None:
    report = check(
        frame(OCTOBER),
        store,
        since=date(2026, 10, 24),
        now=datetime(2026, 10, 26, 11, tzinfo=PARIS),
    )
    assert report.warnings == ["real time: no FR consumption_mw", "real time: no ARA solar_mw"]


def test_a_raw_layer_problem_is_a_check_error(store: RawStore) -> None:
    (store.root / "eco2mix").mkdir()
    (store.root / "eco2mix" / "manifest.jsonl").write_text("")
    (store.root / "eco2mix" / "stray.json.gz").write_bytes(b"x")
    report = check(
        frame([*OCTOBER, *real_time(paris(2026, 10, 26, 10, 45))]),
        store,
        since=date(2026, 10, 24),
        now=datetime(2026, 10, 26, 11, tzinfo=PARIS),
    )
    assert report.errors == ["raw layer: eco2mix/stray.json.gz: not in the manifest"]


def updates(clean: Path) -> dict[str, tuple[datetime, datetime]]:
    """The date each dataset was last updated, and the reception of the response that says so."""
    frame = pl.read_parquet(clean / "eco2mix" / "updates.parquet")
    return {
        row["dataset"]: (row["updated_at"], row["received_at"])
        for row in frame.iter_rows(named=True)
    }


def test_the_catalogue_gives_the_date_each_dataset_was_updated(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.processed = {"eco2mix-national-tr": paris(2026, 6, 20, 14, 45)}
    report = run(odre, store, clean)
    assert not report.failed
    kept = updates(clean)
    assert sorted(kept) == [
        "eco2mix-national-cons-def",
        "eco2mix-national-tr",
        "eco2mix-regional-cons-def",
        "eco2mix-regional-tr",
    ]
    assert kept["eco2mix-national-tr"][0] == paris(2026, 6, 20, 14, 45)
    assert kept["eco2mix-regional-tr"][0] == NOW - timedelta(minutes=10)
    # One request for the four datasets, the last of the run, kept in the raw layer.
    receipt = store.receipts("eco2mix")[-1]
    assert (receipt.dataset, receipt.url) == ("updates", eco2mix.updates_url())
    assert {received_at for _, received_at in kept.values()} == {receipt.received_at}


def catalogue(**dates: object) -> bytes:
    """A response of the catalogue, with these dates for the datasets they name."""
    results = [
        {"dataset_id": f"eco2mix-{key.replace('_', '-')}", "data_processed": value}
        for key, value in dates.items()
    ]
    return json.dumps({"total_count": len(results), "results": results}).encode()


EVERY: dict[str, object] = {
    name: "2026-06-20T12:50:00+00:00"
    for name in ("national_tr", "national_cons_def", "regional_tr", "regional_cons_def")
}
# The second run of these tests, an hour after the first.
SECOND = NOW + timedelta(hours=1)
FUTURE = (SECOND + timedelta(days=1, seconds=1)).astimezone(UTC).isoformat()
A_LIST = json.dumps(
    {"results": [{"dataset_id": ["eco2mix-national-tr"], "data_processed": EVERY["national_tr"]}]}
).encode()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"[]", "no results"),
        (b"{}", "no results"),
        (catalogue(national_tr=EVERY["national_tr"]), "no date for eco2mix-national-cons-def"),
        (A_LIST, "no date for eco2mix-national-cons-def"),
        (
            catalogue(**{**EVERY, "regional_tr": "yesterday"}),
            "unexpected data_processed 'yesterday'",
        ),
        (catalogue(**{**EVERY, "regional_tr": "2026-06-20T12:50:00"}), "unexpected data_processed"),
        (catalogue(**{**EVERY, "regional_tr": None}), "unexpected data_processed None"),
        # A date after the day following the run is faulty metadata, as Enedis's publications
        # (ADR 026), and so is one before 2000, such as the start of the Unix epoch.
        (catalogue(**{**EVERY, "national_tr": FUTURE}), f"unexpected data_processed '{FUTURE}'"),
        (
            catalogue(**{**EVERY, "national_tr": "1970-01-01T00:00:00+00:00"}),
            "unexpected data_processed '1970-01-01T00:00:00+00:00'",
        ),
    ],
    ids=[
        "a list",
        "no results",
        "a dataset missing",
        "an id that is a list",
        "no date",
        "no offset",
        "null",
        "the future",
        "before 2000",
    ],
)
def test_a_faulty_catalogue_is_an_error_and_the_dates_kept_stay(
    odre: FakeOdre, store: RawStore, clean: Path, body: bytes, message: str
) -> None:
    run(odre, store, clean)
    before = updates(clean)
    odre.bodies[eco2mix.updates_url()] = body
    report = run(odre, store, clean, now=SECOND)
    assert [error for error in report.errors if message in error], report.errors
    assert updates(clean) == before
    # The measures are rebuilt all the same: this quarter-hour was measured after the first run.
    assert not report.invalid
    moment = (NOW - timedelta(minutes=45)).astimezone(UTC)
    assert at(measures(clean), "FR", "co2_g_per_kwh", moment)["value"]


def test_a_date_up_to_the_day_after_the_run_is_accepted(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.processed = {"eco2mix-national-tr": NOW + timedelta(days=1)}
    assert not run(odre, store, clean).failed
    assert updates(clean)["eco2mix-national-tr"][0] == NOW + timedelta(days=1)


@pytest.mark.parametrize(
    ("status", "content_type", "message"),
    [(404, "application/json", "404"), (200, "text/html", "text/html")],
    ids=["an error", "a page of maintenance"],
)
@pytest.mark.parametrize("first", [True, False], ids=["first run", "later run"])
def test_a_catalogue_that_fails_is_an_error_and_the_measures_are_written(
    odre: FakeOdre,
    store: RawStore,
    clean: Path,
    status: int,
    content_type: str,
    message: str,
    first: bool,
) -> None:
    if not first:
        run(odre, store, clean)
    before = updates(clean) if not first else None

    def failing(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/catalog/datasets"):
            return httpx2.Response(status, text="<html>", headers={"content-type": content_type})
        return odre.handle(request)

    odre.now = SECOND
    with client(httpx2.MockTransport(failing)) as http:
        report = ingest(http, store, clean, now=SECOND, since=SINCE, sleep=lambda seconds: None)
    assert len(report.errors) == 1 and message in report.errors[0]
    moment = (NOW - timedelta(minutes=45)).astimezone(UTC)
    assert at(measures(clean), "FR", "co2_g_per_kwh", moment)["value"]
    if first:
        assert not (clean / "eco2mix" / "updates.parquet").exists()
    else:
        assert updates(clean) == before
