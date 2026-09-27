"""éCO2mix against a fake ODRÉ: four datasets served by URL, with versions set by each test."""

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import polars as pl
import pytest

from ampere.data.clean import Report
from ampere.data.days import day_bounds, first_pass, quarter_hours
from ampere.data.http import client
from ampere.data.raw import RawStore
from ampere.sources.eco2mix import (
    AUVERGNE_RHONE_ALPES,
    FRANCE,
    SCHEMA,
    Month,
    MonthFile,
    best_version,
    check,
    complete,
    coverage_url,
    ingest,
    month_url,
    months,
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
    """The four éCO2mix datasets: definitive, then consolidated, then real time, as the test sets.

    Real time is measured up to an hour before `now`; RTE's forecast runs to the end of tomorrow.
    """

    def __init__(self) -> None:
        self.definitive_until = paris(2026, 5, 1)
        self.consolidated_until = paris(2026, 6, 1)
        self.now = NOW
        self.lag = timedelta(hours=1)
        self.fields = {level: dict(fields) for level, fields in FIELDS.items()}
        self.gaps: set[tuple[str, datetime]] = set()
        self.bodies: dict[str, bytes] = {}
        self.requests: list[str] = []

    def version(self, moment: datetime) -> str:
        if moment < self.definitive_until:
            return "definitive"
        if moment < self.consolidated_until:
            return "consolidated"
        return "real_time"

    def coverage(self, level: str) -> list[dict[str, str]]:
        step = QUARTER if level == "national" else HALF
        periods = [
            ("definitive", paris(2025, 1, 1), self.definitive_until),
            ("consolidated", self.definitive_until, self.consolidated_until),
        ]
        return [
            {"nature": NATURES[name], "first": first.isoformat(), "last": (end - step).isoformat()}
            for name, first, end in periods
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
        real_time = kind == "tr"
        step = QUARTER if real_time or level == "national" else HALF
        today = datetime.combine(self.now.astimezone(PARIS).date(), datetime.min.time(), PARIS)
        # The national forecast runs to the end of tomorrow; the region stops at the end of today.
        last = (today + timedelta(days=2 if level == "national" else 1)).astimezone(UTC)
        rows: list[dict[str, object]] = []
        moment = start
        while moment < min(end, last if real_time else end):
            version = self.version(moment)
            if (version == "real_time") == real_time:
                local = moment.astimezone(PARIS).isoformat()
                row: dict[str, object] = {
                    "nature": NATURES[version],
                    "date": local[:10],
                    "heure": local[11:16],
                    "date_heure": moment.isoformat(),
                }
                if level == "regional":
                    row = {
                        "code_insee_region": "84",
                        "libelle_region": "Auvergne-Rhône-Alpes",
                        **row,
                    }
                for field in self.fields[level]:
                    row[field] = self.value(field, level, moment, version)
                row["eolien"] = 1234  # a field Ampère keeps in the raw layer but does not read
                rows.append(row)
            moment += step
        return rows

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.bodies:
            return httpx2.Response(
                200, content=self.bodies[url], headers={"content-type": "application/json"}
            )
        match = re.fullmatch(
            r".*/datasets/eco2mix-(national|regional)-(tr|cons-def)/(.*)", request.url.path
        )
        if match is None:
            return httpx2.Response(404)
        level, kind, endpoint = match.groups()
        if endpoint == "records" and kind == "cons-def":
            results = self.coverage(level)
            return httpx2.Response(200, json={"total_count": len(results), "results": results})
        if endpoint == "exports/json":
            where = re.fullmatch(
                r"(?:code_insee_region='84' and )?date_heure >= '(.+)' and date_heure < '(.+)'",
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
) -> Report:
    odre.now = now
    with client(httpx2.MockTransport(odre.handle)) as http:
        sleep = pauses.append if pauses is not None else (lambda seconds: None)
        return ingest(http, store, clean, now=now, since=SINCE, full=full, sleep=sleep)


def measures(clean: Path) -> pl.DataFrame:
    return pl.read_parquet(clean / "eco2mix" / "measures.parquet")


def at(frame: pl.DataFrame, area: str, measure: str, moment: datetime) -> dict[str, object]:
    rows = frame.filter(
        (pl.col("area") == area) & (pl.col("measure") == measure) & (pl.col("start") == moment)
    )
    assert rows.height == 1, rows
    return rows.row(0, named=True)


def asked(odre: FakeOdre) -> list[str]:
    """The requests, named "coverage national" or by dataset and month, "national-tr 2026-06"."""
    names = []
    for request in map(httpx2.URL, odre.requests):
        found = re.search(
            r"eco2mix-((national|regional)-(?:tr|cons-def))/(records|exports)", request.path
        )
        assert found is not None
        if found.group(3) == "records":
            names.append(f"coverage {found.group(2)}")
            continue
        start = re.search(r"date_heure >= '([^']+)'", request.params["where"])
        assert start is not None
        month = datetime.fromisoformat(start.group(1)).astimezone(PARIS)
        names.append(f"{found.group(1)} {month:%Y-%m}")
    return names


def test_months_are_paris_months_bounded_in_utc() -> None:
    assert months(date(2026, 3, 15), date(2026, 4, 2)) == [
        (paris(2026, 3, 1), paris(2026, 4, 1)),  # 31 days less the hour of the clock change
        (paris(2026, 4, 1), paris(2026, 5, 1)),
    ]
    assert months(date(2026, 4, 1), date(2026, 4, 1)) == [(paris(2026, 4, 1), paris(2026, 5, 1))]


@pytest.mark.parametrize(
    ("month", "version"),
    [
        ((paris(2026, 4, 1), paris(2026, 5, 1)), "definitive"),
        ((paris(2026, 5, 1), paris(2026, 6, 1)), "consolidated"),
        ((paris(2026, 6, 1), paris(2026, 7, 1)), "real_time"),
        # Consolidated data up to the 15th only: the month is not covered whole.
        ((paris(2026, 7, 1), paris(2026, 8, 1)), "real_time"),
    ],
)
def test_a_month_takes_the_most_final_version_that_covers_it_whole(
    month: tuple[datetime, datetime], version: str
) -> None:
    coverage = {
        "definitive": (paris(2025, 1, 1), paris(2026, 5, 1) - HALF),
        "consolidated": (paris(2026, 5, 1), paris(2026, 6, 1) - HALF),
    }
    assert best_version(coverage, *month) == version
    partial = {"consolidated": (paris(2026, 7, 1), paris(2026, 7, 15))}
    assert best_version(partial, paris(2026, 7, 1), paris(2026, 8, 1)) == "real_time"


def test_the_urls_name_the_dataset_the_region_and_the_month() -> None:
    april = (paris(2026, 4, 1), paris(2026, 5, 1))
    assert month_url(FRANCE, "cons-def", *april) == (
        f"{API}/eco2mix-national-cons-def/exports/json?where=date_heure+%3E%3D+%27"
        "2026-03-31T22%3A00%3A00%2B00%3A00%27+and+date_heure+%3C+%27"
        "2026-04-30T22%3A00%3A00%2B00%3A00%27"
    )
    assert "where=code_insee_region%3D%2784%27+and+date_heure" in month_url(
        AUVERGNE_RHONE_ALPES, "tr", *april
    )
    assert coverage_url(FRANCE).startswith(f"{API}/eco2mix-national-cons-def/records?select=")
    assert "code_insee_region%3D%2784%27" in coverage_url(AUVERGNE_RHONE_ALPES)


def test_the_first_run_fetches_each_month_from_the_dataset_that_holds_it(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    report = run(odre, store, clean)
    assert report.invalid == [] and report.errors == [] and report.warnings == []
    assert asked(odre) == [
        "coverage national",
        "national-cons-def 2026-04",
        "national-cons-def 2026-05",
        "national-tr 2026-06",
        "coverage regional",
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
    run(odre, store, clean, now=datetime(2026, 6, 25, 15, tzinfo=PARIS))
    assert asked(odre) == [
        "coverage national",
        "national-tr 2026-06",
        "coverage regional",
        "regional-tr 2026-06",
    ]
    # Only June has changed: two new responses, one per zone.
    assert len(sorted(store.root.rglob("*.gz"))) == len(files) + 2


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
    # The real-time response of June stays in the archive, but the consolidated one wins.
    row = at(measures(clean), "FR", "consumption_mw", paris(2026, 6, 10, 7, 15))
    assert (row["value"], row["step_minutes"], row["version"]) == (50_701.0, 30, "consolidated")


def test_a_consolidated_month_that_turns_definitive_is_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    odre.definitive_until = paris(2026, 6, 1)  # May becomes definitive
    odre.requests.clear()
    run(odre, store, clean, now=datetime(2026, 6, 25, 15, tzinfo=PARIS))
    assert "national-cons-def 2026-05" in asked(odre)
    assert "national-cons-def 2026-04" not in asked(odre)
    row = at(measures(clean), "FR", "co2_g_per_kwh", paris(2026, 5, 10, 7))
    assert (row["value"], row["version"]) == (27 + 2, "definitive")


def test_full_asks_for_every_month_again(odre: FakeOdre, store: RawStore, clean: Path) -> None:
    run(odre, store, clean)
    odre.requests.clear()
    run(odre, store, clean, now=datetime(2026, 6, 25, 15, tzinfo=PARIS), full=True)
    assert len(odre.requests) == 8


def test_the_requests_are_spaced_out(odre: FakeOdre, store: RawStore, clean: Path) -> None:
    pauses: list[float] = []
    run(odre, store, clean, pauses=pauses)
    assert pauses == [0.5] * 7


def test_a_month_kept_with_missing_values_is_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    odre.gaps.add(("taux_co2", paris(2026, 4, 10, 7)))
    report = run(odre, store, clean)
    assert report.errors == ["FR co2_g_per_kwh 2026-04-10: 2 of 96 quarter-hours missing"]
    odre.gaps.clear()  # RTE fills the gap
    odre.requests.clear()
    report = run(odre, store, clean, now=datetime(2026, 6, 25, 15, tzinfo=PARIS))
    assert "national-cons-def 2026-04" in asked(odre)
    assert report.errors == []


def test_a_damaged_file_of_an_old_month_is_written_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    run(odre, store, clean)
    april = next(r for r in store.receipts("eco2mix") if r.dataset == "regional-cons-def")
    (store.root / april.path).write_bytes(b"damaged")
    odre.requests.clear()
    report = run(odre, store, clean, now=datetime(2026, 6, 25, 15, tzinfo=PARIS))
    assert "regional-cons-def 2026-04" in asked(odre)
    assert store.read(april)
    assert not [error for error in report.errors if error.startswith("raw layer")]


def test_an_old_month_received_with_an_unknown_shape_is_asked_again(
    odre: FakeOdre, store: RawStore, clean: Path
) -> None:
    april = month_url(FRANCE, "cons-def", paris(2026, 4, 1), paris(2026, 5, 1))
    odre.bodies[april] = b'{"error": "try later"}'
    with pytest.raises(SchemaError, match="not a list of rows"):
        run(odre, store, clean)
    del odre.bodies[april]
    report = run(odre, store, clean, now=datetime(2026, 6, 25, 15, tzinfo=PARIS))
    assert report.errors == [] and report.invalid == []


def test_each_new_response_and_the_whole_run_are_logged(
    odre: FakeOdre, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ampere")
    run(odre, store, clean)
    kept = [record for record in caplog.records if "new response kept" in record.getMessage()]
    assert len(kept) == 8
    assert re.search(r"eco2mix: 8 requests, 8 new responses, \d+ values", caplog.text)


def test_a_value_outside_its_limits_keeps_the_clean_file_as_it_was(
    odre: FakeOdre, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run(odre, store, clean)
    before = (clean / "eco2mix" / "measures.parquet").read_bytes()
    odre.fields["national"]["taux_co2"] = lambda moment: 600.0
    report = run(odre, store, clean, now=datetime(2026, 6, 25, 15, tzinfo=PARIS))
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
        (b'{"results": []}', "not a list of rows"),
        (month_body([["2026-04-10T05:00:00+00:00", 50_700]]), "not an object"),
        (month_body([{**ROW, "date_heure": "2026-04-10T05:00:00"}]), "date_heure"),
        (month_body([{**ROW, "date_heure": 1775797200000}]), "date_heure"),
        (month_body([{**ROW, "nature": "Données provisoires"}]), "nature"),
        (month_body([{**ROW, "consommation": "50700"}]), "consommation"),
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
    with pytest.raises(SchemaError, match=re.escape("https://example.test/month")) as error:
        parse_month(body, "https://example.test/month", FRANCE, "cons-def")
    assert message in str(error.value)


@pytest.mark.parametrize("ghost_first", [False, True], ids=["after", "before"])
def test_the_hour_that_never_happened_is_left_out(ghost_first: bool) -> None:
    # 31 March 2024: ODRÉ gives 02:00 to 02:45, which never happened, the instants of 03:00 and on.
    real = {
        **ROW,
        "date": "2024-03-31",
        "heure": "03:00",
        "date_heure": "2024-03-31T01:00:00+00:00",
    }
    rows: list[object] = [
        {**real, "prevision_j1": 46_646},
        {**real, "heure": "02:00", "prevision_j1": 46_423},
    ]
    body = month_body(list(reversed(rows)) if ghost_first else rows)
    month = parse_month(body, "https://example.test/month", FRANCE, "cons-def")
    assert month.values["rte_forecast_mw"] == [(paris(2024, 3, 31, 3), 46_646.0, "definitive")]


def test_a_regional_month_holds_only_its_region() -> None:
    row = {"code_insee_region": "11", "nature": "Données temps réel"}
    body = month_body([{**row, "date_heure": "2026-06-10T05:00:00+00:00", "solaire": 800}])
    with pytest.raises(SchemaError, match="region '11'"):
        parse_month(body, "https://example.test/month", AUVERGNE_RHONE_ALPES, "tr")


def coverage_body(nature: str, first: object, last: object) -> bytes:
    return json.dumps({"results": [{"nature": nature, "first": first, "last": last}]}).encode()


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"results": {}}',
        # The consolidated-definitive dataset has no real time.
        coverage_body(
            "Données temps réel", "2026-06-01T00:00:00+00:00", "2026-06-30T21:45:00+00:00"
        ),
        coverage_body("Données consolidées", "2026-06-01", None),
    ],
)
def test_a_coverage_must_keep_its_known_shape(body: bytes) -> None:
    with pytest.raises(SchemaError, match=re.escape("https://example.test/coverage")):
        parse_coverage(body, "https://example.test/coverage")


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
    """Every quarter-hour of every measure, from first to last."""
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


def test_the_checks_count_each_measure_until_yesterday_with_the_clock_changes(
    store: RawStore,
) -> None:
    # 25 October 2026 has 100 quarter-hours, but ODRÉ never gives the first pass of 02:00 to 02:59:
    # 96 are expected. The 26th is today, not checked yet.
    rows = [
        row
        for row in whole_days(date(2026, 10, 24), date(2026, 10, 25))
        if not first_pass(row[0])
        and not (row[2] == "solar_mw" and row[0] == paris(2026, 10, 25, 12))
    ]
    now = datetime(2026, 10, 26, 3, tzinfo=PARIS)
    report = check(frame(rows), store, since=date(2026, 10, 24), now=now)
    assert report.errors == ["ARA solar_mw 2026-10-25: 1 of 96 quarter-hours missing"]


def test_an_autumn_month_is_whole_without_the_first_pass_of_the_hour_lived_twice() -> None:
    october = MonthFile(FRANCE, "cons-def", paris(2026, 10, 1), paris(2026, 11, 1))

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
    ],
)
def test_a_value_outside_its_limits_or_after_its_time_is_invalid(
    store: RawStore, row: tuple[datetime, str, str, float, int, str], invalid: str
) -> None:
    rows = [r for r in whole_days(date(2026, 10, 24), date(2026, 10, 25)) if r[:3] != row[:3]]
    now = datetime(2026, 10, 26, 11, tzinfo=PARIS)
    report = check(frame([*rows, row]), store, since=date(2026, 10, 24), now=now)
    assert len(report.invalid) == 1 and invalid in report.invalid[0]


def test_the_forecast_of_tomorrow_and_the_limits_themselves_are_valid(store: RawStore) -> None:
    rows = whole_days(date(2026, 10, 24), date(2026, 10, 25))
    extra = [
        (paris(2026, 10, 27, 23, 45), "FR", "rte_forecast_mw", 10_000.0, 15, "real_time"),
        (paris(2026, 10, 26, 10, 45), "FR", "consumption_mw", 150_000.0, 15, "real_time"),
        (paris(2026, 10, 26, 10, 45), "ARA", "solar_load_factor_pct", 150.0, 15, "real_time"),
    ]
    report = check(
        frame([*rows, *extra]),
        store,
        since=date(2026, 10, 24),
        now=datetime(2026, 10, 26, 11, tzinfo=PARIS),
    )
    assert report.invalid == [] and report.errors == [] and report.warnings == []


@pytest.mark.parametrize(("hours", "warned"), [(3, False), (4, True)])
def test_a_real_time_consumption_more_than_3_hours_old_is_a_warning(
    store: RawStore, hours: int, warned: bool
) -> None:
    rows = whole_days(date(2026, 10, 24), date(2026, 10, 25))
    last = paris(2026, 10, 26, 10, 45)  # the quarter-hour ends at 11:00
    real_time = [(last, "FR", "consumption_mw", 50_000.0, 15, "real_time")]
    now = datetime(2026, 10, 26, 11 + hours, tzinfo=PARIS)
    report = check(frame([*rows, *real_time]), store, since=date(2026, 10, 24), now=now)
    assert bool(report.warnings) is warned


def test_a_raw_layer_problem_is_a_check_error(store: RawStore) -> None:
    (store.root / "eco2mix").mkdir()
    (store.root / "eco2mix" / "manifest.jsonl").write_text("")
    (store.root / "eco2mix" / "stray.json.gz").write_bytes(b"x")
    rows = whole_days(date(2026, 10, 24), date(2026, 10, 25))
    real_time = [(paris(2026, 10, 26, 10, 45), "FR", "consumption_mw", 50_000.0, 15, "real_time")]
    report = check(
        frame([*rows, *real_time]),
        store,
        since=date(2026, 10, 24),
        now=datetime(2026, 10, 26, 11, tzinfo=PARIS),
    )
    assert report.errors == ["raw layer: eco2mix/stray.json.gz: not in the manifest"]
