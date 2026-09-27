"""Enedis against a fake Enedis: the metadata of its native API, and the Parquet exports of the
compatibility layer of its old API."""

import io
import json
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import polars as pl
import pytest

from ampere.data.clean import Report
from ampere.data.days import day_bounds, every_day
from ampere.data.http import UnexpectedResponse, client
from ampere.data.raw import RawStore
from ampere.sources.enedis import (
    CONSUMPTION,
    SOLAR,
    Month,
    check,
    ingest,
    late_months,
    metadata_url,
    months,
    parse_month,
    parse_publication,
)
from ampere.sources.shapes import SchemaError

PARIS = ZoneInfo("Europe/Paris")
HALF = timedelta(minutes=30)
SINCE = date(2026, 4, 1)
PUBLISHED = "2026-07-30T09:51:18.221Z"
# A later publication, first seen at LATER.
REVISED = "2026-08-15T10:00:00.000Z"
NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)
# Past the 7 days during which a publication has every month asked again.
LATER = NOW + timedelta(days=8)
# The filters Ampère must send, written out here rather than taken from the code.
WHERE = {
    "conso-inf36-region": "code_region='84' and startswith(profil, 'RES')",
    "prod-region": "code_region='84' and filiere_de_production='F5 : Solaire' and "
    "plage_de_puissance_injection in ('P1 : ]0 - 3] kW', 'P2 : ]3 - 9] kW')",
}
HOMES = {
    ("RES1 (+ RES1WE)", "P1: ]0-3] kVA"): 1.0,
    ("RES2 (+ RES5)", "P3: ]6-9] kVA"): 2.0,
    # Few sites: the statistical secrecy publishes the mean of each day, but on the 15th.
    ("RES4", "P7: ]18-36] kVA"): 3.0,
}
FLAT = ("RES4", "P7: ]18-36] kVA")
ROOFS = {"P1 : ]0 - 3] kW": 1.0, "P2 : ]3 - 9] kW": 2.0}
# The datasets of Ampère, with only the segments of homes that the fake publishes.
DATASETS = (replace(CONSUMPTION, expected=tuple(HOMES)), SOLAR)


def paris(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=PARIS).astimezone(UTC)


def home(moment: datetime, factor: float) -> float:
    """The mean curve of a segment, in Wh per half-hour: a shape over the Paris day."""
    local = moment.astimezone(PARIS)
    return factor * (200 + 10 * local.hour + local.minute // 3)


def roof(moment: datetime, factor: float) -> float:
    local = moment.astimezone(PARIS)
    return factor * max(0, 6 - abs(local.hour - 13)) * 50.0


class FakeEnedis:
    """Enedis's two APIs, as they behave.

    The native API gives the metadata of a dataset, with the date of its last publication. The
    compatibility layer exports the rows asked for as Parquet, over a published window of whole
    Paris days, with instants in UTC and no time zone; a month outside the window comes back
    empty. The same rows give the same bytes.
    """

    def __init__(self) -> None:
        self.now = NOW
        self.published = {"conso-inf36-region": PUBLISHED, "prod-region": PUBLISHED}
        # Another field of the metadata, which may change without a new publication.
        self.count = 1
        self.first = date(2026, 4, 1)
        self.last = date(2026, 6, 30)
        # Added to the curves, when a test revises them.
        self.revision = 0.0
        # Half-hours masked by the statistical secrecy: (segment, instant).
        self.masked: set[tuple[tuple[str, ...], datetime]] = set()
        # Rows Enedis leaves out: (segment, instant).
        self.missing: set[tuple[tuple[str, ...], datetime]] = set()
        # Days whose count of sites grows by one at noon, in Paris: (segment, day).
        self.site_changes: set[tuple[tuple[str, ...], date]] = set()
        # Days at zero from end to end: (segment, day).
        self.zero_days: set[tuple[tuple[str, ...], date]] = set()
        # Instants where only curve n° 2 is masked: (segment, instant).
        self.masked_second: set[tuple[tuple[str, ...], datetime]] = set()
        self.bodies: dict[str, bytes] = {}
        self.statuses: dict[str, int] = {}
        self.requests: list[str] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.statuses:
            return httpx2.Response(self.statuses[url], text="refused")
        if request.url.host != "opendata.enedis.fr":
            return httpx2.Response(404)
        native = re.fullmatch(r"/data-fair/api/v1/datasets/([a-z0-9-]+)", request.url.path)
        if native is not None:
            name = native.group(1)
            body = (
                self.bodies.get(url)
                or json.dumps(
                    {
                        "id": "x",
                        "slug": name,
                        "dataUpdatedAt": self.published[name],
                        "count": self.count,
                    }
                ).encode()
            )
            return httpx2.Response(
                200, content=body, headers={"content-type": "application/json; charset=utf-8"}
            )
        export = re.fullmatch(
            r"/api/explore/v2\.1/catalog/datasets/([a-z0-9-]+)/exports/parquet", request.url.path
        )
        if export is None:
            return httpx2.Response(404)
        name = export.group(1)
        where = re.fullmatch(
            re.escape(WHERE[name]) + r" and horodate >= '(.+)' and horodate < '(.+)'",
            request.url.params["where"],
        )
        assert where is not None, request.url.params["where"]
        start, end = (datetime.fromisoformat(bound) for bound in where.groups())
        body = self.bodies.get(url) or self.parquet(name, start, end)
        return httpx2.Response(
            200, content=body, headers={"content-type": "application/vnd.apache.parquet"}
        )

    def parquet(self, name: str, start: datetime, end: datetime) -> bytes:
        rows: list[dict[str, object]] = []
        first = max(start, day_bounds(self.first)[0])
        stop = min(end, day_bounds(self.last)[1])
        segments: list[tuple[tuple[str, ...], float, Callable[[datetime, float], float]]] = (
            [(segment, factor, home) for segment, factor in HOMES.items()]
            if name == "conso-inf36-region"
            else [((power_range,), factor, roof) for power_range, factor in ROOFS.items()]
        )
        moment = first
        while moment < stop:
            for segment, factor, curve in segments:
                row = self.row(segment, moment, factor, curve)
                if row is not None:
                    rows.append(row)
            moment += HALF
        return frame(name, rows)

    def row(
        self,
        segment: tuple[str, ...],
        moment: datetime,
        factor: float,
        curve: Callable[[datetime, float], float],
    ) -> dict[str, object] | None:
        if (segment, moment) in self.missing:
            return None
        local = moment.astimezone(PARIS)
        sites = int(1000 * factor) + local.day
        if (segment, local.date()) in self.site_changes and local.hour >= 12:
            sites += 1
        if (segment, local.date()) in self.zero_days:
            mean = 0.0
        elif segment == FLAT and local.day != 15:
            start, end = day_bounds(local.date())
            steps = [start + i * HALF for i in range((end - start) // HALF)]
            mean = sum(curve(step, factor) for step in steps) / len(steps)
        else:
            mean = curve(moment, factor)
        mean += self.revision
        # The secrecy masks the curves, and the total of rooftop solar with them, never that of
        # homes.
        masked = (segment, moment) in self.masked
        solar = len(segment) == 1
        curve_value = None if masked else mean
        # Each half of a large segment may stand for less than 1 % of its sites.
        index = (
            "S" if masked else "< 1" if segment == ("RES1 (+ RES1WE)", "P1: ]0-3] kVA") else "35"
        )
        row: dict[str, object] = {
            "horodate": moment.replace(tzinfo=None),
            "region": "Auvergne-Rhône-Alpes",
            "code_region": "84",
            "total": None if masked and solar else mean * sites,
            "sites": sites,
            "mean_1": None if curve_value is None else curve_value * 1.1,
            "index_1": index,
            "mean_2": None
            if curve_value is None or (segment, moment) in self.masked_second
            else curve_value * 0.9,
            "index_2": "S" if (segment, moment) in self.masked_second else index,
            "mean": curve_value,
            "index": index,
        }
        if solar:
            row["plage"] = segment[0]
            row["filiere"] = "F5 : Solaire"
        else:
            row["profil"], row["plage"] = segment
        return row


def frame(name: str, rows: list[dict[str, object]]) -> bytes:
    """A Parquet export with Enedis's columns, as it names and types them."""
    consumption = name == "conso-inf36-region"
    columns: dict[str, tuple[str, pl.DataType]] = {
        "horodate": ("horodate", pl.Datetime("ms")),
        "region": ("region", pl.String()),
        "code_region": ("code_region", pl.String()),
        **(
            {
                "profil": ("profil", pl.String()),
                "plage": ("plage_de_puissance_souscrite", pl.String()),
                "sites": ("nb_points_soutirage", pl.Int32()),
                "total": ("total_energie_soutiree_wh", pl.Float64()),
            }
            if consumption
            else {
                "plage": ("plage_de_puissance_injection", pl.String()),
                "filiere": ("filiere_de_production", pl.String()),
                "sites": ("nb_points_injection", pl.Int32()),
                "total": ("total_energie_injectee_wh", pl.Float64()),
            }
        ),
        "mean_1": ("courbe_moyenne_ndeg1_wh", pl.Float64()),
        "index_1": ("indice_representativite_courbe_ndeg1", pl.String()),
        "mean_2": ("courbe_moyenne_ndeg2_wh", pl.Float64()),
        "index_2": ("indice_representativite_courbe_ndeg2", pl.String()),
        "mean": ("courbe_moyenne_ndeg1_ndeg2_wh", pl.Float64()),
        "index": ("indice_representativite_courbe_ndeg1_ndeg2", pl.String()),
    }
    data = {name: [row.get(key) for row in rows] for key, (name, _) in columns.items()}
    schema = {name: dtype for name, dtype in columns.values()}
    table = pl.DataFrame(data, schema=schema).with_columns(
        pl.lit("0").alias("jour_max_du_mois_0_1"), pl.lit("0").alias("semaine_max_du_mois_0_1")
    )
    buffer = io.BytesIO()
    table.write_parquet(buffer)
    return buffer.getvalue()


@pytest.fixture
def fake() -> FakeEnedis:
    return FakeEnedis()


@pytest.fixture
def store(tmp_path: Path, fake: FakeEnedis) -> RawStore:
    # Responses are received at the time the fake lives in.
    return RawStore.create(tmp_path / "raw", clock=lambda: fake.now)


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    return tmp_path / "clean"


def run(
    fake: FakeEnedis,
    store: RawStore,
    clean: Path,
    now: datetime = NOW,
    full: bool = False,
    pauses: list[float] | None = None,
    since: date = SINCE,
) -> Report:
    fake.now = now
    with client(httpx2.MockTransport(fake.handle)) as http:
        sleep = pauses.append if pauses is not None else (lambda seconds: None)
        return ingest(
            http, store, clean, now=now, since=since, full=full, sleep=sleep, datasets=DATASETS
        )


def table(clean: Path, kind: str) -> pl.DataFrame:
    return pl.read_parquet(clean / "enedis" / f"{kind}.parquet")


def at(
    frame: pl.DataFrame, segment: tuple[str, ...], measure: str, moment: datetime
) -> dict[str, object]:
    keys = ["profile", "power_range"] if len(segment) == 2 else ["power_range"]
    rows = frame.filter(
        pl.all_horizontal(pl.col(key) == value for key, value in zip(keys, segment, strict=True))
        & (pl.col("measure") == measure)
        & (pl.col("start") == moment)
    )
    assert rows.height == 1, rows
    return rows.row(0, named=True)


def asked(fake: FakeEnedis) -> list[str]:
    """The requests, named "metadata conso-inf36-region" or "conso-inf36-region 2026-04"."""
    names = []
    for url in map(httpx2.URL, fake.requests):
        native = re.fullmatch(r"/data-fair/api/v1/datasets/(.+)", url.path)
        if native is not None:
            names.append(f"metadata {native.group(1)}")
            continue
        name = url.path.split("/")[-3]
        bound = re.search(r"horodate >= '([^']+)'", url.params["where"])
        assert bound is not None
        names.append(f"{name} {datetime.fromisoformat(bound.group(1)).astimezone(PARIS):%Y-%m}")
    return names


MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
EVERYTHING = [
    "metadata conso-inf36-region",
    *(f"conso-inf36-region {month}" for month in MONTHS),
    "metadata prod-region",
    *(f"prod-region {month}" for month in MONTHS),
]
METADATA = ["metadata conso-inf36-region", "metadata prod-region"]


def test_the_urls_ask_for_the_metadata_and_for_a_month_of_the_segments_of_ampere() -> None:
    assert metadata_url(CONSUMPTION) == (
        "https://opendata.enedis.fr/data-fair/api/v1/datasets/conso-inf36-region"
    )
    (april,) = months(SOLAR, date(2026, 4, 1), date(2026, 4, 30))
    url = httpx2.URL(april.url)
    assert (url.host, url.path) == (
        "opendata.enedis.fr",
        "/api/explore/v2.1/catalog/datasets/prod-region/exports/parquet",
    )
    assert url.params["where"] == (
        f"{WHERE['prod-region']} and horodate >= '2026-03-31T22:00:00+00:00' "
        "and horodate < '2026-04-30T22:00:00+00:00'"
    )
    assert april.name == "2026-04"


def test_the_first_run_fetches_every_month_of_both_datasets(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    assert run(fake, store, clean) == Report()
    assert asked(fake) == EVERYTHING
    kept = [(receipt.dataset, receipt.request) for receipt in store.receipts("enedis")]
    assert kept == [
        ("publication", "conso-inf36-region"),
        *(("consumption", month) for month in MONTHS),
        ("publication", "prod-region"),
        *(("solar", month) for month in MONTHS),
    ]
    assert store.receipts("enedis")[1].path.endswith(".parquet.gz")


def test_the_clean_tables_hold_each_half_hour_in_w(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    homes = table(clean, "consumption")
    assert homes.schema == CONSUMPTION.schema
    segment = ("RES2 (+ RES5)", "P3: ]6-9] kVA")
    moment = paris(2026, 5, 12, 19, 30)
    wh = home(moment, 2.0)
    assert at(homes, segment, "mean_w", moment)["value"] == pytest.approx(2 * wh)
    assert at(homes, segment, "mean_1_w", moment)["value"] == pytest.approx(2 * wh * 1.1)
    assert at(homes, segment, "mean_2_w", moment)["value"] == pytest.approx(2 * wh * 0.9)
    assert at(homes, segment, "total_w", moment)["value"] == pytest.approx(2 * wh * 2012)
    sites = at(homes, segment, "sites", moment)
    assert (sites["value"], sites["step_minutes"]) == (2012, 1440)
    assert at(homes, segment, "mean_w", moment)["step_minutes"] == 30
    roofs = table(clean, "solar")
    assert roofs.schema == SOLAR.schema
    noon = paris(2026, 6, 21, 13)
    assert at(roofs, ("P2 : ]3 - 9] kW",), "mean_w", noon)["value"] == pytest.approx(
        2 * roof(noon, 2.0)
    )


def test_a_day_the_secrecy_publishes_as_its_mean_keeps_a_step_of_a_day(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    homes = table(clean, "consumption")
    flat = at(homes, FLAT, "mean_w", paris(2026, 5, 14, 19))
    peak = at(homes, FLAT, "mean_w", paris(2026, 5, 15, 19))
    assert flat["step_minutes"] == 1440
    assert peak["step_minutes"] == 30
    assert peak["value"] == pytest.approx(2 * home(paris(2026, 5, 15, 19), 3.0))


def test_a_masked_value_stays_absent_and_is_no_error(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    segment = ("RES1 (+ RES1WE)", "P1: ]0-3] kVA")
    moment = paris(2026, 5, 12, 3)
    fake.masked.add((segment, moment))
    assert run(fake, store, clean) == Report()
    homes = table(clean, "consumption")
    at_moment = homes.filter((pl.col("profile") == segment[0]) & (pl.col("start") == moment))
    # The curves are masked; the count of sites and the total stay.
    assert sorted(at_moment["measure"].cast(str)) == ["sites", "total_w"]
    # A curve with a masked half-hour is not flat for that.
    assert at(homes, segment, "mean_w", moment + HALF)["step_minutes"] == 30


def test_a_masked_total_is_no_error_but_a_missing_row_is_one(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    roofs = ("P1 : ]0 - 3] kW",)
    fake.masked.add((roofs, paris(2026, 5, 12, 21)))
    fake.missing.add((roofs, paris(2026, 5, 13, 21)))
    report = run(fake, store, clean)
    assert report == Report(errors=["solar P1 : ]0 - 3] kW 2026-05-13: 1 of 48 half-hours missing"])
    measures = table(clean, "solar").filter(
        (pl.col("power_range") == roofs[0]) & (pl.col("start") == paris(2026, 5, 12, 21))
    )
    # For rooftop solar, the secrecy masks the total with the curves: the count of sites stays.
    assert measures["measure"].cast(str).to_list() == ["sites"]
    # The step of a day needs every half-hour of it, for a count of sites too.
    solar = table(clean, "solar")
    assert at(solar, roofs, "sites", paris(2026, 5, 13, 12))["step_minutes"] == 30
    assert at(solar, roofs, "sites", paris(2026, 5, 14, 12))["step_minutes"] == 1440


def test_a_curve_masked_alone_leaves_the_others_without_error(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    segment = ("RES2 (+ RES5)", "P3: ]6-9] kVA")
    moment = paris(2026, 5, 12, 3)
    fake.masked_second.add((segment, moment))
    assert run(fake, store, clean) == Report()
    measures = table(clean, "consumption").filter(
        (pl.col("profile") == segment[0]) & (pl.col("start") == moment)
    )
    assert sorted(measures["measure"].cast(str)) == ["mean_1_w", "mean_w", "sites", "total_w"]


def test_a_count_of_sites_that_changes_within_a_day_keeps_the_half_hour(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    segment = ("RES2 (+ RES5)", "P3: ]6-9] kVA")
    fake.site_changes.add((segment, date(2026, 5, 13)))
    run(fake, store, clean)
    homes = table(clean, "consumption")
    morning = at(homes, segment, "sites", paris(2026, 5, 13, 11))
    afternoon = at(homes, segment, "sites", paris(2026, 5, 13, 13))
    assert (morning["value"], morning["step_minutes"]) == (2013, 30)
    assert (afternoon["value"], afternoon["step_minutes"]) == (2014, 30)
    assert at(homes, segment, "sites", paris(2026, 5, 12, 11))["step_minutes"] == 1440


def test_a_curve_at_zero_all_day_keeps_the_half_hour(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    # A mean of zero over a day means zero at each half-hour: nothing is negative.
    roofs = ("P1 : ]0 - 3] kW",)
    fake.zero_days.add((roofs, date(2026, 5, 20)))
    run(fake, store, clean)
    zero = at(table(clean, "solar"), roofs, "mean_w", paris(2026, 5, 20, 13))
    assert (zero["value"], zero["step_minutes"]) == (0.0, 30)


def test_a_flat_day_with_a_masked_half_hour_keeps_the_half_hour(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    # The step of a day needs every half-hour of it.
    fake.masked.add((FLAT, paris(2026, 5, 14, 3)))
    run(fake, store, clean)
    assert (
        at(table(clean, "consumption"), FLAT, "mean_w", paris(2026, 5, 14, 19))["step_minutes"]
        == 30
    )


@pytest.mark.parametrize(
    ("first", "last", "day", "half_hours", "published", "now"),
    [
        (date(2026, 3, 1), date(2026, 3, 31), date(2026, 3, 29), 46, "2026-04-10T10:00:00Z", 4),
        (date(2025, 10, 1), date(2025, 10, 31), date(2025, 10, 26), 50, "2025-11-10T10:00:00Z", 11),
    ],
)
def test_a_flat_day_of_a_clock_change_keeps_the_step_of_a_day(
    fake: FakeEnedis,
    store: RawStore,
    clean: Path,
    first: date,
    last: date,
    day: date,
    half_hours: int,
    published: str,
    now: int,
) -> None:
    fake.first, fake.last = first, last
    fake.published = dict.fromkeys(fake.published, published)
    when = datetime(first.year, now, 20, tzinfo=UTC)
    assert run(fake, store, clean, now=when, since=first) == Report()
    start, end = day_bounds(day)
    flat = table(clean, "consumption").filter(
        (pl.col("profile") == FLAT[0])
        & (pl.col("measure") == "mean_w")
        & (pl.col("start") >= start)
        & (pl.col("start") < end)
    )
    assert flat.height == half_hours
    assert flat["step_minutes"].unique().to_list() == [1440]


def test_a_publication_missing_from_the_clean_table_is_a_warning(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    # As after a run cut short: the publication has a new quarter, the clean table does not.
    fake.published = dict.fromkeys(fake.published, "2026-09-15T10:00:00.000Z")
    report = run(fake, store, clean, now=datetime(2026, 9, 20, tzinfo=UTC))
    assert report.warnings == [
        f"{kind}: the clean table ends at 2026-06-30 22:00 UTC, more than 60 days before the "
        "publication of 2026-09-15: run --full"
        for kind in ("consumption", "solar")
    ]


def test_a_month_neither_published_nor_kept_is_missing_for_good(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    # Once the window has slid, only a backup of the raw layer can bring its first months back.
    fake.first = date(2026, 5, 1)
    report = run(fake, store, clean)
    assert len(report.errors) == 5 * 30  # three segments of homes and two of roofs, all April
    assert "consumption RES4 P7: ]18-36] kVA 2026-04-15: 48 of 48 half-hours missing" in (
        report.errors
    )


def test_a_row_missing_on_the_first_day_is_an_error(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    roofs = ("P2 : ]3 - 9] kW",)
    fake.missing.add((roofs, paris(2026, 4, 1, 12)))
    report = run(fake, store, clean)
    assert report == Report(errors=["solar P2 : ]3 - 9] kW 2026-04-01: 1 of 48 half-hours missing"])


def test_nothing_published_is_an_error(fake: FakeEnedis, store: RawStore, clean: Path) -> None:
    fake.first = date(2026, 9, 1)
    report = run(fake, store, clean)
    assert report == Report(errors=["consumption: nothing published", "solar: nothing published"])


def test_a_month_not_published_comes_back_empty_and_is_no_error(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    homes = table(clean, "consumption")
    assert homes["start"].max() == paris(2026, 7, 1) - HALF
    july = next(r for r in store.receipts("enedis") if r.request == "2026-07")
    assert pl.read_parquet(io.BytesIO(store.read(july))).height == 0


def test_a_new_publication_has_every_month_asked_for_7_days(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.requests.clear()
    # The first publication ever seen dates from more than 7 days: nothing more to ask.
    run(fake, store, clean, now=NOW + timedelta(days=1))
    assert asked(fake) == METADATA
    kept = len(store.receipts("enedis"))
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.requests.clear()
    assert run(fake, store, clean, now=LATER) == Report()
    assert asked(fake) == EVERYTHING
    assert len(store.receipts("enedis")) == kept + 2  # the months are the same bytes


@pytest.mark.parametrize(
    ("delay", "everything"), [(timedelta(days=7, seconds=-1), True), (timedelta(days=7), False)]
)
def test_every_month_is_asked_until_7_days_after_the_first_reception(
    fake: FakeEnedis, store: RawStore, clean: Path, delay: timedelta, everything: bool
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, REVISED)
    run(fake, store, clean, now=LATER)
    fake.requests.clear()
    run(fake, store, clean, now=LATER + delay)
    assert asked(fake) == (EVERYTHING if everything else METADATA)


def test_the_first_publication_seen_is_new_for_7_days_from_its_own_date(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    fake.published = dict.fromkeys(fake.published, "2026-08-08T10:00:00.000Z")
    run(fake, store, clean)
    fake.requests.clear()
    run(fake, store, clean, now=datetime(2026, 8, 15, 9, tzinfo=UTC))
    assert asked(fake) == EVERYTHING
    fake.requests.clear()
    run(fake, store, clean, now=datetime(2026, 8, 15, 11, tzinfo=UTC))
    assert asked(fake) == METADATA


def test_metadata_that_change_without_a_publication_do_not_start_it_again(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, REVISED)
    run(fake, store, clean, now=LATER)
    fake.count = 2  # a new metadata response, with the same publication
    run(fake, store, clean, now=LATER + timedelta(days=5))
    fake.requests.clear()
    run(fake, store, clean, now=LATER + timedelta(days=7))
    assert asked(fake) == METADATA


def test_a_publication_that_comes_back_counts_from_its_return(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, "2026-08-11T10:00:00.000Z")
    run(fake, store, clean, now=NOW + timedelta(days=1))
    fake.published = dict.fromkeys(fake.published, PUBLISHED)  # Enedis takes it back
    run(fake, store, clean, now=NOW + timedelta(days=10))
    fake.requests.clear()
    run(fake, store, clean, now=NOW + timedelta(days=12))
    assert asked(fake) == EVERYTHING


def test_a_revised_publication_is_kept_and_used(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.revision = 1.0
    fake.last = date(2026, 7, 31)
    fake.requests.clear()
    assert run(fake, store, clean, now=LATER) == Report()
    assert asked(fake) == EVERYTHING
    segment = ("RES1 (+ RES1WE)", "P1: ]0-3] kVA")
    moment = paris(2026, 5, 12, 19, 30)
    homes = table(clean, "consumption")
    assert at(homes, segment, "mean_w", moment)["value"] == pytest.approx(
        2 * (home(moment, 1.0) + 1)
    )
    assert homes["start"].max() == paris(2026, 8, 1) - HALF  # July is now published
    fake.requests.clear()
    run(fake, store, clean, now=LATER + timedelta(days=8))
    assert asked(fake) == METADATA


def test_a_month_enedis_no_longer_publishes_keeps_its_last_version(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    # The window slides: April leaves it.
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.first = date(2026, 5, 1)
    report = run(fake, store, clean, now=LATER)
    assert report == Report()
    april = [
        r for r in store.receipts("enedis") if (r.dataset, r.request) == ("consumption", "2026-04")
    ]
    assert len(april) == 2  # the empty answer is kept too
    homes = table(clean, "consumption")
    assert homes["start"].min() == paris(2026, 4, 1)
    april_values = homes.filter(pl.col("start") == paris(2026, 4, 12))
    assert april_values["received_at"].unique().to_list() == [NOW]
    fake.requests.clear()
    assert run(fake, store, clean, now=LATER + timedelta(days=8)) == Report()
    assert asked(fake) == METADATA


def test_an_empty_month_between_published_ones_is_a_warning_and_asked_again(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    (may,) = months(CONSUMPTION, date(2026, 5, 1), date(2026, 5, 1))
    fake.bodies[may.url] = frame("conso-inf36-region", [])
    fake.published = dict.fromkeys(fake.published, REVISED)
    report = run(fake, store, clean, now=LATER)
    assert report == Report(
        warnings=[
            "consumption 2026-05: the last response is empty, the one received at 2026-08-10 "
            "12:00:00 UTC is used"
        ]
    )
    assert table(clean, "consumption").filter(pl.col("start") == paris(2026, 5, 12)).height > 0
    fake.requests.clear()
    del fake.bodies[may.url]
    assert run(fake, store, clean, now=LATER + timedelta(days=8)) == Report()
    assert asked(fake) == [METADATA[0], "conso-inf36-region 2026-05", METADATA[1]]


def test_a_publication_with_fewer_rows_replaces_the_one_before(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    # The last publication wins: the half-hour it lacks is an error, not a reason to keep the
    # values it revises.
    run(fake, store, clean)
    segment = ("RES2 (+ RES5)", "P3: ]6-9] kVA")
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.revision = 1.0
    fake.missing.add((segment, paris(2026, 5, 12, 3)))
    report = run(fake, store, clean, now=LATER)
    assert report == Report(
        errors=["consumption RES2 (+ RES5) P3: ]6-9] kVA 2026-05-12: 1 of 48 half-hours missing"]
    )
    moment = paris(2026, 5, 12, 19, 30)
    assert at(table(clean, "consumption"), segment, "mean_w", moment)["value"] == pytest.approx(
        2 * (home(moment, 2.0) + 1)
    )


def test_a_damaged_month_is_asked_again_outside_the_7_days(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.revision = 1.0
    run(fake, store, clean, now=LATER)
    # June, the last month published: unreadable is not empty, even at the end of the window.
    june = [r for r in store.receipts("enedis") if (r.dataset, r.request) == ("solar", "2026-06")]
    assert len(june) == 2
    (store.root / june[-1].path).write_bytes(b"damaged")
    fake.requests.clear()
    report = run(fake, store, clean, now=LATER + timedelta(days=8))
    # The last response is the one that counts, although the one before still reads.
    assert asked(fake) == [*METADATA, "prod-region 2026-06"]
    # The damaged file comes back whole from an identical response.
    assert report == Report()
    assert store.verify("enedis") == []


def test_a_damaged_older_response_is_left_aside(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.revision = 1.0
    run(fake, store, clean, now=LATER)
    older = next(
        r for r in store.receipts("enedis") if (r.dataset, r.request) == ("consumption", "2026-05")
    )
    (store.root / older.path).write_bytes(b"damaged")
    report = run(fake, store, clean, now=LATER + timedelta(days=8))
    assert report.errors == [f"raw layer: {older.path}: Not a gzipped file (b'da')"]
    segment = ("RES1 (+ RES1WE)", "P1: ]0-3] kVA")
    moment = paris(2026, 5, 12, 19, 30)
    assert at(table(clean, "consumption"), segment, "mean_w", moment)["value"] == pytest.approx(
        2 * (home(moment, 1.0) + 1)
    )


def test_a_faulty_month_is_an_error_and_the_latest_readable_response_serves(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.revision = 1.0
    run(fake, store, clean, now=LATER)
    (may,) = months(CONSUMPTION, date(2026, 5, 1), date(2026, 5, 1))
    fake.bodies[may.url] = b"not parquet"
    report = run(fake, store, clean, now=LATER + timedelta(days=1))
    assert report.errors[0].startswith(f"{may.url}: not a Parquet export")
    assert len(report.errors) == 1
    assert report.warnings == [
        "consumption 2026-05: the last response does not read, the one received at 2026-08-18 "
        "12:00:00 UTC is used"
    ]
    segment = ("RES1 (+ RES1WE)", "P1: ]0-3] kVA")
    moment = paris(2026, 5, 12, 19, 30)
    assert at(table(clean, "consumption"), segment, "mean_w", moment)["value"] == pytest.approx(
        2 * (home(moment, 1.0) + 1)
    )


def test_faulty_metadata_is_an_error_and_only_missing_months_are_asked(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    fake.bodies[metadata_url(CONSUMPTION)] = json.dumps({"dataUpdatedAt": "yesterday"}).encode()
    report = run(fake, store, clean)
    assert report.errors == [
        f"{metadata_url(CONSUMPTION)}: no date of publication, dataUpdatedAt 'yesterday'"
    ]
    assert asked(fake) == EVERYTHING  # nothing kept yet: every month is missing
    fake.requests.clear()
    run(fake, store, clean, now=NOW + timedelta(days=1))
    assert asked(fake) == METADATA


def test_faulty_metadata_start_the_7_days_again_at_the_next_reception(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.published = dict.fromkeys(fake.published, REVISED)
    run(fake, store, clean, now=LATER)
    fake.bodies[metadata_url(CONSUMPTION)] = b"<html>"
    run(fake, store, clean, now=LATER + timedelta(days=1))
    del fake.bodies[metadata_url(CONSUMPTION)]
    run(fake, store, clean, now=LATER + timedelta(days=2))
    fake.requests.clear()
    run(fake, store, clean, now=LATER + timedelta(days=8))
    # What Enedis published before the faulty response is unknown: for consumption, the 7 days
    # start again at the reception that follows it; for solar, they are over.
    assert asked(fake) == [
        "metadata conso-inf36-region",
        *(f"conso-inf36-region {month}" for month in MONTHS),
        "metadata prod-region",
    ]


def test_an_error_of_enedis_stops_the_run(fake: FakeEnedis, store: RawStore, clean: Path) -> None:
    fake.statuses[metadata_url(SOLAR)] = 503
    with pytest.raises(httpx2.HTTPStatusError, match="503"):
        run(fake, store, clean)


def test_full_asks_for_every_month_again(fake: FakeEnedis, store: RawStore, clean: Path) -> None:
    run(fake, store, clean)
    fake.requests.clear()
    run(fake, store, clean, now=LATER, full=True)
    assert asked(fake) == EVERYTHING


def test_the_requests_are_spaced_out(fake: FakeEnedis, store: RawStore, clean: Path) -> None:
    pauses: list[float] = []
    run(fake, store, clean, pauses=pauses)
    assert pauses == [0.5] * (len(EVERYTHING) - 1)


def test_the_run_is_logged(
    fake: FakeEnedis, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    run(fake, store, clean)
    assert caplog.text.count("new response kept in") == len(EVERYTHING)
    assert f"enedis: {len(EVERYTHING)} requests, {len(EVERYTHING)} new responses" in caplog.text
    assert "every month asked" not in caplog.text
    fake.published = dict.fromkeys(fake.published, REVISED)
    run(fake, store, clean, now=LATER)
    assert (
        "conso-inf36-region: publication of 2026-08-15 10:00:00+00:00, every month asked"
        in caplog.text
    )


def test_a_month_larger_than_the_limit_is_refused(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    (april,) = months(CONSUMPTION, date(2026, 4, 1), date(2026, 4, 1))
    fake.bodies[april.url] = b"x" * 20_000_001
    with pytest.raises(UnexpectedResponse, match="more than 20000000 bytes"):
        run(fake, store, clean)


def test_a_value_outside_its_limits_keeps_the_clean_file_as_it_was(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    before = table(clean, "solar")
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.revision = 5_000.0  # above 9 000 W once doubled
    report = run(fake, store, clean, now=LATER)
    assert any("outside 0 to 9000" in problem for problem in report.invalid)
    assert table(clean, "solar").equals(before)


def test_invalid_values_of_one_dataset_leave_the_other_replaced(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    before = table(clean, "consumption")
    (may,) = months(CONSUMPTION, date(2026, 5, 1), date(2026, 5, 1))
    rows = pl.read_parquet(io.BytesIO(fake.parquet("conso-inf36-region", may.start, may.end)))
    buffer = io.BytesIO()
    rows.with_columns(pl.col("total_energie_soutiree_wh").neg()).write_parquet(buffer)
    fake.bodies[may.url] = buffer.getvalue()
    fake.published = dict.fromkeys(fake.published, REVISED)
    fake.revision = 1.0
    report = run(fake, store, clean, now=LATER)
    assert report.invalid
    assert all("UTC: consumption " in problem for problem in report.invalid)
    assert table(clean, "consumption").equals(before)
    noon = paris(2026, 6, 21, 13)
    assert at(table(clean, "solar"), ("P2 : ]3 - 9] kW",), "mean_w", noon)["value"] == (
        pytest.approx(2 * (roof(noon, 2.0) + 1))
    )


MAY = Month(CONSUMPTION, paris(2026, 5, 1), paris(2026, 6, 1))
URL = "https://example.test/may"


def first_null(column: str) -> list[dict[str, object]]:
    rows = may_rows()
    rows[0][column] = None
    return rows


def may_rows(**changes: object) -> list[dict[str, object]]:
    fake = FakeEnedis()
    table_bytes = fake.parquet("conso-inf36-region", MAY.start, MAY.end)
    rows = pl.read_parquet(io.BytesIO(table_bytes)).head(3).to_dicts()
    for row in rows:
        row.update(changes)
    return rows


def export(rows: list[dict[str, object]], drop: str | None = None) -> bytes:
    table = pl.DataFrame(rows)
    if drop is not None:
        table = table.drop(drop)
    buffer = io.BytesIO()
    table.write_parquet(buffer)
    return buffer.getvalue()


def test_an_empty_export_is_a_month_not_published() -> None:
    empty = FakeEnedis()
    empty.first = date(2026, 7, 1)
    content = empty.parquet("conso-inf36-region", MAY.start, MAY.end)
    assert parse_month(content, MAY, URL).height == 0


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not parquet", "not a Parquet export"),
        (export(may_rows(), drop="nb_points_soutirage"), "no column nb_points_soutirage"),
        (export(may_rows(nb_points_soutirage="12")), "no column nb_points_soutirage"),
        (export(may_rows(horodate=datetime(2026, 4, 15, tzinfo=UTC))), "no column horodate"),
        (export(may_rows(horodate=datetime(2026, 4, 15))), "rows outside the month"),
        # 1 June, 00:00 in Paris: the end of May, already outside it.
        (export(may_rows(horodate=datetime(2026, 5, 31, 22))), "rows outside the month"),
        (export(may_rows(horodate=datetime(2026, 5, 15, 10, 10))), "instants off the half-hour"),
        (export(may_rows(code_region="11")), "a code_region other than 84"),
        (export(may_rows(profil="PRO1 (+ PRO1WE)")), "a profil that does not start with RES"),
        (export([*may_rows(), *may_rows()]), "a half-hour twice for a segment"),
        (export(may_rows(indice_representativite_courbe_ndeg1="12%")), "an unexpected indice"),
        (export(may_rows(courbe_moyenne_ndeg1_wh=float("nan"))), "not a finite number"),
        (
            export(may_rows(total_energie_soutiree_wh=Decimal("1.5"))),
            "no column total_energie_soutiree_wh of the expected type",
        ),
        (export(first_null("horodate")), "a row without horodate"),
        (export(first_null("code_region")), "a row without code_region"),
        (export(first_null("profil")), "a row without profil"),
        (export(first_null("nb_points_soutirage")), "a row without nb_points_soutirage"),
        (
            export(first_null("indice_representativite_courbe_ndeg1")),
            "a row without indice_representativite_courbe_ndeg1",
        ),
        # A curve is missing exactly when its index says that the secrecy masks it.
        (
            export(first_null("courbe_moyenne_ndeg1_ndeg2_wh")),
            "a courbe_moyenne_ndeg1_ndeg2_wh missing without "
            "indice_representativite_courbe_ndeg1_ndeg2 at S",
        ),
        (
            export(may_rows(indice_representativite_courbe_ndeg2="S")),
            "a courbe_moyenne_ndeg2_wh missing without indice_representativite_courbe_ndeg2 at S, "
            "or the reverse",
        ),
        (
            export(first_null("total_energie_soutiree_wh")),
            "a total_energie_soutiree_wh missing where the curves are published",
        ),
    ],
)
def test_a_month_must_keep_its_known_shape(content: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=re.escape(message)) as error:
        parse_month(content, MAY, URL)
    assert str(error.value).startswith(f"{URL}: ")


def test_a_month_is_bounded_by_its_footer_before_it_is_read() -> None:
    # Parquet compresses by itself: a small file could unfold into gigabytes of repeated rows.
    rows = pl.DataFrame(may_rows())
    many = rows.sample(n=500_001, with_replacement=True, seed=1)
    with pytest.raises(SchemaError, match="500001 rows in 15 columns, more than a month"):
        parse_month(export(many.to_dicts()), MAY, URL)
    wide = rows.with_columns(pl.lit(0).alias(f"extra_{i}") for i in range(16))
    with pytest.raises(SchemaError, match="3 rows in 31 columns, more than a month"):
        parse_month(export(wide.to_dicts()), MAY, URL)


def test_a_column_ampere_does_not_read_is_left_unread() -> None:
    values = parse_month(export(may_rows(commentaire="une note")), MAY, URL)
    assert "commentaire" not in values.columns
    assert values.height > 0


def test_a_response_with_a_row_outside_its_month_leaves_it_to_the_one_before(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    (may,) = months(CONSUMPTION, date(2026, 5, 1), date(2026, 5, 1))
    good = fake.parquet("conso-inf36-region", may.start, may.end)
    rows = pl.read_parquet(io.BytesIO(good))
    stray = rows.head(1).with_columns(pl.col("horodate") + timedelta(days=40))
    buffer = io.BytesIO()
    pl.concat([rows, stray]).write_parquet(buffer)
    fake.bodies[may.url] = buffer.getvalue()
    fake.published = dict.fromkeys(fake.published, REVISED)
    report = run(fake, store, clean, now=LATER)
    assert report.errors == [f"{may.url}: rows outside the month"]
    assert report.warnings == [
        "consumption 2026-05: the last response does not read, the one received at 2026-08-10 "
        "12:00:00 UTC is used"
    ]


@pytest.mark.parametrize(
    ("owner", "name", "message"),
    [
        (pl, "scan_parquet", "not a Parquet export"),
        (pl, "read_parquet", "not a Parquet export"),
        (pl.DataFrame, "is_duplicated", "rows Polars cannot check"),
    ],
)
def test_a_panic_of_polars_is_a_faulty_month(
    monkeypatch: pytest.MonkeyPatch, owner: object, name: str, message: str
) -> None:
    # The footer, the columns and the checks of the rows: Polars may panic in each.
    content = export(may_rows())

    def panic(*args: object, **options: object) -> pl.DataFrame:
        raise pl.exceptions.PanicException("index out of bounds")

    monkeypatch.setattr(owner, name, panic)
    with pytest.raises(SchemaError, match=message):
        parse_month(content, MAY, URL)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"[]", "no date of publication"),
        (b'{"dataUpdatedAt": "2026-07-30T09:51:18"}', "no date of publication"),
        (b"<html>", "not JSON"),
        (b'{"dataUpdatedAt": "9999-12-31T23:59:59-01:00"}', "no date of publication"),
    ],
)
def test_the_metadata_must_give_the_date_of_the_publication(body: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=message):
        parse_publication(body, URL)
    assert parse_publication(b'{"dataUpdatedAt": "2026-07-30T09:51:18.221Z"}', URL) == datetime(
        2026, 7, 30, 9, 51, 18, 221000, tzinfo=UTC
    )


# The dataset of the rows below, with their segment alone expected.
RES3 = replace(CONSUMPTION, expected=(("RES3", "P1: ]0-9] kVA"),))


def clean_rows(first: date, last: date) -> pl.DataFrame:
    """Whole days of sites and totals for one segment."""
    rows = []
    for day in every_day(first, last):
        start, end = day_bounds(day)
        for i in range((end - start) // HALF):
            for measure in ("sites", "total_w"):
                rows.append((start + i * HALF, "RES3", "P1: ]0-9] kVA", measure, 10.0, 30, NOW))
    return pl.DataFrame(rows, schema=CONSUMPTION.schema, orient="row")


def test_the_checks_count_each_half_hour_of_a_segment_with_the_clock_changes() -> None:
    # The clocks go forward on 29 March 2026: 46 half-hours. A half-hour without its count of
    # sites is missing; one without its total is masked by the statistical secrecy.
    rows = clean_rows(date(2026, 3, 28), date(2026, 3, 31))
    moment = paris(2026, 3, 29, 4)
    missing = rows.filter(
        ~((pl.col("start") == moment) & (pl.col("measure") == "sites"))
        & ~((pl.col("start") == moment + HALF) & (pl.col("measure") == "total_w"))
    )
    report = check(missing, RES3, since=date(2026, 3, 28), now=datetime(2026, 4, 2, tzinfo=UTC))
    assert report == Report(
        errors=["consumption RES3 P1: ]0-9] kVA 2026-03-29: 1 of 46 half-hours missing"]
    )


def test_the_checks_stop_at_the_last_half_hour_published() -> None:
    rows = clean_rows(date(2026, 10, 24), date(2026, 10, 31))  # 25 October: 50 half-hours
    now = datetime(2026, 12, 1, tzinfo=UTC)
    assert check(rows, RES3, since=date(2026, 10, 24), now=now) == Report()
    gap = rows.filter(
        ~((pl.col("start") == paris(2026, 10, 25, 12)) & (pl.col("measure") == "sites"))
    )
    assert check(gap, RES3, since=date(2026, 10, 24), now=now) == Report(
        errors=["consumption RES3 P1: ]0-9] kVA 2026-10-25: 1 of 50 half-hours missing"]
    )


@pytest.mark.parametrize(("days", "warned"), [(150, False), (151, True)])
def test_a_publication_late_by_a_month_is_a_warning(days: int, warned: bool) -> None:
    rows = clean_rows(date(2026, 6, 30), date(2026, 6, 30))
    end = paris(2026, 7, 1)
    report = check(rows, RES3, since=date(2026, 6, 30), now=end + timedelta(days=days))
    expected = (
        "consumption: the last half-hour published ends at 2026-06-30 22:00 UTC, more than 150 "
        "days ago"
    )
    assert report == Report(warnings=[expected] if warned else [])


@pytest.mark.parametrize(
    ("end", "warned"),
    [(paris(2026, 7, 1), False), (paris(2026, 6, 30), True), (paris(2026, 7, 1, 12), True)],
)
def test_a_table_that_ends_within_a_month_is_a_warning(end: datetime, warned: bool) -> None:
    # Enedis publishes whole months: the checks, which stop at the last half-hour published,
    # would not see the rest of the last one missing.
    rows = clean_rows(date(2026, 6, 1), date(2026, 7, 1)).filter(pl.col("start") < end)
    report = check(rows, RES3, since=date(2026, 6, 1), now=NOW)
    expected = (
        f"consumption: the last half-hour published ends at {end:%Y-%m-%d %H:%M} UTC, before "
        "the end of its month"
    )
    assert (expected in report.warnings) is warned


def test_an_expected_segment_never_published_is_an_error_and_another_a_warning() -> None:
    rows = clean_rows(date(2026, 6, 1), date(2026, 6, 30))
    other = replace(CONSUMPTION, expected=(("RES4", "P1: ]0-9] kVA"),))
    report = check(rows, other, since=date(2026, 6, 1), now=NOW)
    assert report == Report(
        errors=["consumption RES4 P1: ]0-9] kVA: nothing published"],
        warnings=["consumption RES3 P1: ]0-9] kVA: not an expected segment"],
    )


def test_ampere_expects_the_39_segments_of_homes_and_the_2_of_rooftops() -> None:
    assert len(set(CONSUMPTION.expected)) == 39
    assert ("RES2WE", "P6: ]15-36] kVA") in CONSUMPTION.expected
    assert set(HOMES) < set(CONSUMPTION.expected)
    assert SOLAR.expected == (("P1 : ]0 - 3] kW",), ("P2 : ]3 - 9] kW",))


@pytest.mark.parametrize(
    ("gap", "warned"), [(timedelta(days=60), False), (timedelta(days=60, seconds=1), True)]
)
def test_a_publication_more_than_60_days_after_the_clean_table_is_a_warning(
    gap: timedelta, warned: bool
) -> None:
    last = paris(2026, 6, 30, 23, 30)
    rows = pl.DataFrame(
        [(last, "RES3", "P1: ]0-9] kVA", "sites", 10.0, 1440, NOW)],
        schema=CONSUMPTION.schema,
        orient="row",
    )
    assert bool(late_months(rows, CONSUMPTION, last + gap)) is warned
    assert late_months(rows, CONSUMPTION, None) == []


@pytest.mark.parametrize(
    ("measure", "value", "problem"),
    [
        ("sites", -1.0, "sites -1, below 0"),
        ("total_w", -0.5, "total_w -0.5, below 0"),
        ("mean_w", 36_000.5, "mean_w 36000.5, outside 0 to 36000"),
        ("mean_2_w", -1.0, "mean_2_w -1, outside 0 to 36000"),
    ],
)
def test_a_value_outside_its_limits_is_invalid(measure: str, value: float, problem: str) -> None:
    moment = paris(2026, 5, 12, 10)
    rows = pl.DataFrame(
        [(moment, "RES3", "P1: ]0-9] kVA", measure, value, 30, NOW)],
        schema=CONSUMPTION.schema,
        orient="row",
    )
    report = check(rows, CONSUMPTION, since=date(2026, 5, 12), now=NOW)
    assert report.invalid == [f"2026-05-12 08:00 UTC: consumption RES3 P1: ]0-9] kVA {problem}"]


def test_an_instant_off_the_grid_is_invalid() -> None:
    # A half-hour twice is a fault of the shape of its month, never an invalid row.
    moment = paris(2026, 5, 12, 10)
    rows = pl.DataFrame(
        [
            (moment, "RES3", "P1: ]0-9] kVA", "mean_w", 10.0, 30, NOW),
            (moment + timedelta(minutes=15), "RES3", "P1: ]0-9] kVA", "mean_w", 10.0, 30, NOW),
        ],
        schema=CONSUMPTION.schema,
        orient="row",
    )
    report = check(rows, CONSUMPTION, since=date(2026, 5, 12), now=NOW)
    assert report.invalid == [
        "2026-05-12 08:15:00 UTC: consumption RES3 P1: ]0-9] kVA mean_w not on the half-hour",
    ]


def test_a_solar_mean_above_9_kw_is_invalid() -> None:
    moment = paris(2026, 6, 21, 13)
    rows = pl.DataFrame(
        [(moment, "P1 : ]0 - 3] kW", "mean_w", 9_000.5, 30, NOW)],
        schema=SOLAR.schema,
        orient="row",
    )
    assert check(rows, SOLAR, since=date(2026, 6, 21), now=NOW).invalid == [
        "2026-06-21 11:00 UTC: solar P1 : ]0 - 3] kW mean_w 9000.5, outside 0 to 9000"
    ]


def test_a_raw_layer_problem_is_a_check_error(
    fake: FakeEnedis, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    (store.root / "enedis" / "stray.parquet.gz").write_bytes(b"x")
    report = run(fake, store, clean, now=LATER)
    assert report.errors == ["raw layer: enedis/stray.parquet.gz: not in the manifest"]
