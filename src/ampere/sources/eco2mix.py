"""éCO2mix, from RTE on ODRÉ: the carbon intensity, consumption and J-1 forecast of France, and
the solar output of Auvergne-Rhône-Alpes (ADR 024).

ODRÉ keeps each figure in up to three versions: real time, then consolidated, then definitive. A run
asks ODRÉ which periods the consolidated and definitive versions cover, then fetches the Paris
months that can still change, up to 14 days after their end, those that a more final version now
covers, and those the raw layer does not hold whole. It keeps every response in the raw layer,
rebuilds clean/eco2mix/measures.parquet from it alone, and checks the result.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx2
import polars as pl

from ampere.data.clean import Report, quarter_hours_per_day, write_parquet
from ampere.data.days import (
    QUARTER_HOUR,
    day_bounds,
    every_day,
    first_pass,
    never_happened,
    paris_day,
)
from ampere.data.raw import DamagedRawFile, RawStore
from ampere.sources.archive import fetch_json
from ampere.sources.shapes import SchemaError, is_number, load_json

log = logging.getLogger(__name__)

SOURCE = "eco2mix"
API = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets"
# The start of the history: the first day of the Enedis window, shared by every source.
SINCE = date(2023, 7, 1)
# A month can change until this long after its end, like a week of SMARD (ADR 023).
SETTLED_AFTER = timedelta(days=14)
# Seconds between two requests, out of politeness.
PAUSE = 0.5
# The largest response accepted: a month of national data weighs 2.6 MB.
MAX_BYTES = 20_000_000
# Real time comes about an hour late: later than this, its feed has stopped.
STALE_AFTER = timedelta(hours=3)
HALF_HOUR = timedelta(minutes=30)
# The two datasets of each level on ODRÉ: real time, and consolidated or definitive.
TR, CONS_DEF = "tr", "cons-def"
# The versions of a figure, from the least to the most final, and their names on ODRÉ.
VERSIONS = ("real_time", "consolidated", "definitive")
NATURES = {
    "Données temps réel": "real_time",
    "Données consolidées": "consolidated",
    "Données définitives": "definitive",
}
FORECAST = "rte_forecast_mw"


@dataclass(frozen=True)
class Measure:
    """A figure of éCO2mix: its name in the clean table, its field on ODRÉ, and its limits."""

    name: str
    field: str
    lowest: float
    highest: float
    # Its step in the consolidated and definitive data; real time comes by the quarter-hour.
    settled_step: timedelta


@dataclass(frozen=True)
class Area:
    """A zone of éCO2mix and the measures Ampère takes from it."""

    name: str
    level: str
    region: str | None
    measures: tuple[Measure, ...]


FRANCE = Area(
    "FR",
    "national",
    None,
    (
        Measure("consumption_mw", "consommation", 10_000, 150_000, HALF_HOUR),
        Measure("co2_g_per_kwh", "taux_co2", 0, 500, HALF_HOUR),
        Measure(FORECAST, "prevision_j1", 10_000, 150_000, QUARTER_HOUR),
    ),
)
AUVERGNE_RHONE_ALPES = Area(
    "ARA",
    "regional",
    "84",
    (
        Measure("solar_mw", "solaire", 0, 20_000, HALF_HOUR),
        # Above 100 % when RTE's installed capacity lags behind the real one.
        Measure("solar_load_factor_pct", "tch_solaire", 0, 150, HALF_HOUR),
    ),
)
AREAS = (FRANCE, AUVERGNE_RHONE_ALPES)

SCHEMA = pl.Schema(
    {
        "start": pl.Datetime("us", "UTC"),
        "area": pl.Enum([area.name for area in AREAS]),
        "measure": pl.Enum([measure.name for area in AREAS for measure in area.measures]),
        "value": pl.Float64(),
        "step_minutes": pl.UInt8(),
        "version": pl.Enum(list(VERSIONS)),
        "received_at": pl.Datetime("us", "UTC"),
    }
)


@dataclass
class Month:
    """The values of a monthly response, per measure: (start, value, version) for each instant."""

    values: dict[str, list[tuple[datetime, float, str]]]
    versions: set[str] = field(default_factory=set)

    @property
    def version(self) -> str:
        """The least final version among its rows."""
        return min(self.versions, key=VERSIONS.index, default=VERSIONS[0])


@dataclass(frozen=True)
class MonthFile:
    """A Paris month of one zone, exported from one of its datasets."""

    area: Area
    kind: str
    start: datetime
    end: datetime

    @property
    def dataset(self) -> str:
        return dataset(self.area, self.kind)

    @property
    def url(self) -> str:
        return month_url(self.area, self.kind, self.start, self.end)


def dataset(area: Area, kind: str) -> str:
    """The name of a dataset in the raw layer: its name on ODRÉ, without "eco2mix-"."""
    return f"{area.level}-{kind}"


def coverage_url(area: Area) -> str:
    """The first and last instants of the consolidated and definitive versions of a zone."""
    query = {
        "select": "nature, min(date_heure) as first, max(date_heure) as last",
        "group_by": "nature",
    }
    if area.region is not None:
        query["where"] = f"code_insee_region='{area.region}'"
    return f"{API}/eco2mix-{dataset(area, CONS_DEF)}/records?{urlencode(query)}"


def month_url(area: Area, kind: str, start: datetime, end: datetime) -> str:
    """Every field of a zone over a month, exported from one dataset."""
    where = f"date_heure >= '{start.isoformat()}' and date_heure < '{end.isoformat()}'"
    if area.region is not None:
        where = f"code_insee_region='{area.region}' and {where}"
    return f"{API}/eco2mix-{dataset(area, kind)}/exports/json?{urlencode({'where': where})}"


def months(first: date, last: date) -> list[tuple[datetime, datetime]]:
    """The Paris months from the one holding `first` to the one holding `last`, bounded in UTC."""
    bounds = []
    month = first.replace(day=1)
    while month <= last:
        following = (month + timedelta(days=31)).replace(day=1)
        bounds.append((day_bounds(month)[0], day_bounds(following)[0]))
        month = following
    return bounds


def best_version(
    coverage: dict[str, tuple[datetime, datetime]], start: datetime, end: datetime
) -> str:
    """The most final version that covers a whole month, down to its last half-hour."""

    def covers(first: datetime, last: datetime) -> bool:
        return first <= start and end - HALF_HOUR <= last

    if "definitive" in coverage and covers(*coverage["definitive"]):
        return "definitive"
    periods = coverage.values()
    if periods and covers(min(first for first, _ in periods), max(last for _, last in periods)):
        return "consolidated"
    return "real_time"


def ingest(
    http: httpx2.Client,
    store: RawStore,
    clean: Path,
    *,
    now: datetime,
    since: date = SINCE,
    full: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Fetch the months that can still change, that a more final version now covers, or that the
    raw layer lacks, every month if full; then rebuild the clean measures, check them, and replace
    the clean file if its rows are valid."""
    kept = len(store.receipts(SOURCE))
    asked = 0

    def ask(name: str, url: str) -> bytes:
        nonlocal asked
        if asked:
            sleep(PAUSE)
        asked += 1
        return fetch_json(
            http, store, source=SOURCE, dataset=name, url=url, max_bytes=MAX_BYTES, sleep=sleep
        )

    for area in AREAS:
        coverage = parse_coverage(ask("coverage", coverage_url(area)), coverage_url(area))
        for start, end in months(since, paris_day(now)):
            best = best_version(coverage, start, end)
            file = MonthFile(area, TR if best == "real_time" else CONS_DEF, start, end)
            if full or not settled(store, file, best, now):
                parse_month(ask(file.dataset, file.url), file.url, area, file.kind)
    measures = build(store, since=since)
    log.info(
        "eco2mix: %d requests, %d new responses, %d values",
        asked,
        len(store.receipts(SOURCE)) - kept,
        measures.height,
    )
    report = check(measures, store, since=since, now=now)
    path = clean / SOURCE / "measures.parquet"
    if report.invalid:
        log.error("%s kept as it was: the new values break its rules", path)
    else:
        write_parquet(measures, path)
    return report


def settled(store: RawStore, file: MonthFile, best: str, now: datetime) -> bool:
    """Whether the raw layer holds a month for good, so that a run can skip it.

    The month must have ended more than SETTLED_AFTER ago, and its last response must read, be of
    the most final version ODRÉ has, and give every value of the month at its step.
    """
    if now < file.end + SETTLED_AFTER:
        return False
    last = store.last(SOURCE, file.dataset, file.url)
    if last is None:
        return False
    try:
        month = parse_month(store.read(last), file.url, file.area, file.kind)
    except (DamagedRawFile, SchemaError) as error:
        log.warning("%s: the kept response is unusable, asked again (%s)", file.url, error)
        return False
    if VERSIONS.index(month.version) < VERSIONS.index(best):
        log.info("%s: ODRÉ now has %s data, asked again", file.url, best)
        return False
    if not complete(month, file):
        log.warning("%s: the kept response lacks values, asked again", file.url)
        return False
    return True


def complete(month: Month, file: MonthFile) -> bool:
    """Whether a monthly response has a value for every instant ODRÉ gives, at its step."""
    for measure in file.area.measures:
        step = QUARTER_HOUR if file.kind == TR else measure.settled_step
        expected = published(file.start, file.end, step)
        if not expected <= {start for start, _, _ in month.values[measure.name]}:
            return False
    return True


def published(start: datetime, end: datetime, step: timedelta) -> set[datetime]:
    """The instants ODRÉ gives between two instants: every step, but for the first pass of the
    hour lived twice when the clocks go back, since it counts that hour only once."""
    instants = (start + i * step for i in range((end - start) // step))
    return {moment for moment in instants if not first_pass(moment)}


def parse_coverage(content: bytes, url: str) -> dict[str, tuple[datetime, datetime]]:
    """The first and last instants of each version in a consolidated-definitive dataset."""
    data = load_json(content, url)
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise SchemaError(f"{url}: no list of results")
    coverage = {}
    for row in rows:
        nature = row.get("nature") if isinstance(row, dict) else None
        version = NATURES.get(nature) if isinstance(nature, str) else None
        if version not in ("consolidated", "definitive"):
            raise SchemaError(f"{url}: unexpected period {row!r}")
        coverage[version] = (
            instant(row.get("first"), url, "first"),
            instant(row.get("last"), url, "last"),
        )
    return coverage


def parse_month(content: bytes, url: str, area: Area, kind: str) -> Month:
    """The values of a monthly export, after a check of the shape of every row."""
    rows = load_json(content, url)
    if not isinstance(rows, list):
        raise SchemaError(f"{url}: not a list of rows")
    by_instant: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SchemaError(f"{url}: a row that is not an object: {row!r}")
        if area.region is not None and row.get("code_insee_region") != area.region:
            raise SchemaError(f"{url}: a row of region {row.get('code_insee_region')!r}")
        start = instant(row.get("date_heure"), url, "date_heure")
        if start.minute % 15 or start.second or start.microsecond:
            raise SchemaError(f"{url}: {start:%Y-%m-%d %H:%M:%S} UTC is off the quarter-hour grid")
        other = by_instant.setdefault(start, row)
        if other is row:
            continue
        # When the clocks go forward, ODRÉ also gives 02:00 to 02:45, which never happened, the
        # instants of 03:00 to 03:45: the same measures, and a forecast only repeated.
        if phantom(other):
            by_instant[start] = row
        elif not phantom(row):
            raise SchemaError(f"{url}: two rows for {start:%Y-%m-%d %H:%M} UTC")
    month = Month({measure.name: [] for measure in area.measures})
    # Per measure: its field, where its values go, and the minutes its instants must divide.
    measures = [
        (measure.field, month.values[measure.name], step_minutes(measure, kind))
        for measure in area.measures
    ]
    for start, row in by_instant.items():
        nature = row.get("nature")
        version = NATURES.get(nature) if isinstance(nature, str) else None
        if version is None:
            raise SchemaError(f"{url}: unexpected nature {nature!r}")
        month.versions.add(version)
        for name, values, step in measures:
            value = row.get(name)
            if value is None:
                continue
            if not is_number(value):
                raise SchemaError(f"{url}: unexpected {name} {value!r} at {start} UTC")
            if start.minute % step:
                raise SchemaError(
                    f"{url}: a {name} value at {start:%Y-%m-%d %H:%M} UTC, between two half-hours"
                )
            values.append((start, float(value), version))
    return month


def phantom(row: dict[str, Any]) -> bool:
    """Whether a row stands for a Paris hour that never happened."""
    try:
        local = datetime.strptime(f"{row.get('date')} {row.get('heure')}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return never_happened(local)


def build(store: RawStore, *, since: date) -> pl.DataFrame:
    """The clean measures, rebuilt from the last response of each month in the raw layer.

    A half-hour value covers its two quarter-hours. For each quarter-hour, the most final version
    wins, then the response received last. An absent value stays absent.
    """
    frames = [
        values_frame(
            parse_month(store.read(receipt), receipt.request, area, kind),
            area,
            kind,
            receipt.received_at,
        )
        for area in AREAS
        for kind in (TR, CONS_DEF)
        for receipt in store.latest(SOURCE, dataset(area, kind))
    ]
    values = pl.concat(frames) if frames else SCHEMA.to_frame()
    return (
        values.with_columns(quarter=pl.int_ranges(0, pl.col("step_minutes").cast(pl.Int64) // 15))
        .explode("quarter", empty_as_null=False)
        .with_columns(pl.col("start") + pl.duration(minutes=pl.col("quarter") * 15))
        .drop("quarter")
        .filter(pl.col("start") >= day_bounds(since)[0])
        .sort(
            ["area", "measure", "start", "version", "received_at"],
            descending=[False, False, False, True, True],
        )
        .unique(["area", "measure", "start"], keep="first", maintain_order=True)
    )


def values_frame(month: Month, area: Area, kind: str, received_at: datetime) -> pl.DataFrame:
    """The values of one response, one row per measure and instant, at their published step."""
    rows = [
        (start, area.name, measure.name, value, step_minutes(measure, kind), version, received_at)
        for measure in area.measures
        for start, value, version in month.values[measure.name]
    ]
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def step_minutes(measure: Measure, kind: str) -> int:
    return 15 if kind == TR else measure.settled_step // timedelta(minutes=1)


def check(measures: pl.DataFrame, store: RawStore, *, since: date, now: datetime) -> Report:
    """Invalid rows; errors for each measure up to yesterday; a warning when real time is late.

    Today is not checked: real time comes about an hour late.
    """
    report = Report(invalid=invalid_rows(measures, now=now))
    counts = {
        (area, name, day): count
        for area, name, day, count in quarter_hours_per_day(measures, by=["area", "measure"]).rows()
    }
    days = {
        day: len(published(*day_bounds(day), QUARTER_HOUR))
        for day in every_day(since, paris_day(now) - timedelta(days=1))
    }
    for area in AREAS:
        for measure in area.measures:
            for day, expected in days.items():
                found = counts.get((area.name, measure.name, day), 0)
                if found < expected:
                    report.errors.append(
                        f"{area.name} {measure.name} {day}: {expected - found} of {expected} "
                        "quarter-hours missing"
                    )
    latest = measures.filter(
        (pl.col("area") == FRANCE.name)
        & (pl.col("measure") == "consumption_mw")
        & (pl.col("version") == "real_time")
    )["start"].max()
    if not isinstance(latest, datetime):
        report.warnings.append("real time: no consumption")
    elif now - (latest + QUARTER_HOUR) > STALE_AFTER:
        report.warnings.append(
            f"real time: the last consumption ends at {latest + QUARTER_HOUR:%Y-%m-%d %H:%M} UTC"
        )
    report.errors.extend(f"raw layer: {problem}" for problem in store.verify(SOURCE))
    return report


def invalid_rows(measures: pl.DataFrame, *, now: datetime) -> list[str]:
    """The rows the clean file must not hold: a value outside the limits of its measure, a measure
    dated after the run, or RTE's forecast dated after tomorrow."""
    after_tomorrow = day_bounds(paris_day(now) + timedelta(days=1))[1]
    invalid: list[str] = []
    for area in AREAS:
        for measure in area.measures:
            rows = measures.filter(
                (pl.col("area") == area.name) & (pl.col("measure") == measure.name)
            )
            outside = rows.filter(~pl.col("value").is_between(measure.lowest, measure.highest))
            invalid.extend(
                f"{start:%Y-%m-%d %H:%M} UTC: {area.name} {measure.name} {value}, "
                f"outside {measure.lowest:g} to {measure.highest:g}"
                for start, value in outside.select("start", "value").rows()
            )
            if measure.name == FORECAST:
                ahead, when = rows.filter(pl.col("start") >= after_tomorrow), "after tomorrow"
            else:
                ahead, when = rows.filter(pl.col("start") > now.astimezone(UTC)), "after the run"
            invalid.extend(
                f"{start:%Y-%m-%d %H:%M} UTC: {area.name} {measure.name} dated {when}"
                for start in ahead["start"]
            )
    return invalid


def instant(value: object, url: str, name: str) -> datetime:
    """An instant of ODRÉ, written in ISO 8601 with its offset, in UTC."""
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if moment.utcoffset() is not None:
                return moment.astimezone(UTC)
    raise SchemaError(f"{url}: unexpected {name} {value!r}")
