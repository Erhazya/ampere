"""éCO2mix, from RTE on ODRÉ: the carbon intensity, consumption and J-1 forecast of France, and
the solar output of Auvergne-Rhône-Alpes (ADR 024).

ODRÉ keeps each figure in up to three versions: real time, then consolidated, then definitive. A run
asks ODRÉ which periods each version covers, then fetches the Paris months that can still change,
up to 14 days after their end, those that a more final version now covers, and those the raw layer
does not hold whole. It keeps every response in the raw layer, rebuilds
clean/eco2mix/measures.parquet month by month from the responses it has chosen, and checks it.
"""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx2
import polars as pl

from ampere.data.clean import Report, instants_per_day, write_parquet
from ampere.data.days import (
    PARIS,
    QUARTER_HOUR,
    day_bounds,
    every_day,
    first_pass,
    never_happened,
    paris_day,
    quarter_hours,
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
# After this hour in Paris, RTE's forecast of tomorrow should be out.
PUBLISHED_BY = 14
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
    """A zone of éCO2mix, the measures Ampère takes from it, and the one whose delay it watches."""

    name: str
    level: str
    region: str | None
    measures: tuple[Measure, ...]
    watched: str


FRANCE = Area(
    "FR",
    "national",
    None,
    (
        Measure("consumption_mw", "consommation", 10_000, 150_000, HALF_HOUR),
        Measure("co2_g_per_kwh", "taux_co2", 0, 500, HALF_HOUR),
        Measure(FORECAST, "prevision_j1", 10_000, 150_000, QUARTER_HOUR),
    ),
    "consumption_mw",
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
    "solar_mw",
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
    def version(self) -> str | None:
        """The least final version among its rows; None for a response without any row."""
        return min(self.versions, key=VERSIONS.index, default=None)


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


def coverage_url(area: Area, kind: str) -> str:
    """The first and last instants of each version of a zone in one of its datasets."""
    query = {
        "select": "nature, min(date_heure) as first, max(date_heure) as last",
        "group_by": "nature",
    }
    if area.region is not None:
        query["where"] = f"code_insee_region='{area.region}'"
    return f"{API}/eco2mix-{dataset(area, kind)}/records?{urlencode(query)}"


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
    periods: dict[str, tuple[datetime, datetime]], start: datetime, end: datetime
) -> str:
    """The most final version that covers a whole month, down to its last half-hour."""

    def covers(first: datetime, last: datetime) -> bool:
        return first <= start and end - HALF_HOUR <= last

    if "definitive" in periods and covers(*periods["definitive"]):
        return "definitive"
    spans = [periods[version] for version in ("consolidated", "definitive") if version in periods]
    if spans and covers(min(first for first, _ in spans), max(last for _, last in spans)):
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
    raw layer lacks, every month if full; then rebuild the clean measures month by month, check
    them, and replace the clean file if its rows are valid."""
    kept = len(store.receipts(SOURCE))
    first = day_bounds(since)[0]
    plan: list[tuple[MonthFile, Month, datetime]] = []
    errors: list[str] = []
    warnings: list[str] = []
    asked = 0

    def ask(name: str, url: str) -> tuple[bytes, datetime]:
        nonlocal asked
        if asked:
            sleep(PAUSE)
        asked += 1
        content, receipt = fetch_json(
            http, store, source=SOURCE, dataset=name, url=url, max_bytes=MAX_BYTES, sleep=sleep
        )
        return content, receipt.received_at

    def periods(area: Area, kind: str) -> dict[str, tuple[datetime, datetime]]:
        url = coverage_url(area, kind)
        return parse_coverage(ask("coverage", url)[0], url, kind, first)

    for area in AREAS:
        settled_data = periods(area, CONS_DEF)
        real_time = periods(area, TR)["real_time"]
        # Up to the month of tomorrow: on the last day of a month, RTE's forecast is in the next.
        for start, end in months(since, paris_day(now) + timedelta(days=1)):
            best = best_version(settled_data, start, end)
            file = MonthFile(area, TR if best == "real_time" else CONS_DEF, start, end)
            name = f"{area.name} {start.astimezone(PARIS):%Y-%m}"
            held = kept_month(store, file)
            # A real-time month whose start has left ODRÉ's window would come back cut: it stays
            # as it was kept, even with full.
            whole = file.kind == CONS_DEF or real_time[0] <= start
            if held is None or (whole and (full or not settled(held[0], file, best, now))):
                content, received_at = ask(file.dataset, file.url)
                try:
                    held = parse_month(content, file), received_at
                except SchemaError as error:
                    # A faulty month is an error; the run goes on with what was kept for it.
                    errors.append(str(error))
                else:
                    version = held[0].version
                    if best != "real_time" and (
                        version is None or VERSIONS.index(version) < VERSIONS.index(best)
                    ):
                        warnings.append(
                            f"{name}: ODRÉ announces {best} data, but its export is "
                            f"{version or 'empty'}"
                        )
            other = MonthFile(area, CONS_DEF if file.kind == TR else TR, start, end)
            kept_other = kept_month(store, other)
            if kept_other is not None and (
                held is None or rank(kept_other[0], other) > rank(held[0], file)
            ):
                if held is None:
                    why = f"{file.dataset} has no usable response"
                elif rank(kept_other[0], other)[0] > rank(held[0], file)[0]:
                    why = f"it covers more quarter-hours than {file.dataset}"
                else:
                    why = f"it is more final than {file.dataset}"
                warnings.append(f"{name}: the kept {other.dataset} response is used: {why}")
                file, held = other, kept_other
            if held is not None:
                plan.append((file, *held))
    measures = build(plan)
    log.info(
        "eco2mix: %d requests, %d new responses, %d values",
        asked,
        len(store.receipts(SOURCE)) - kept,
        measures.height,
    )
    report = check(measures, store, since=since, now=now)
    report.errors[:0] = errors
    report.warnings[:0] = warnings
    path = clean / SOURCE / "measures.parquet"
    if report.invalid:
        log.error("%s kept as it was: the new values break its rules", path)
    else:
        write_parquet(measures, path)
    return report


def kept_month(store: RawStore, file: MonthFile) -> tuple[Month, datetime] | None:
    """The last response kept for a month, read, with its reception time; None if there is none
    or if it no longer reads, damaged or of an unknown shape."""
    last = store.last(SOURCE, file.dataset, file.url)
    if last is None:
        return None
    try:
        return parse_month(store.read(last), file), last.received_at
    except (DamagedRawFile, SchemaError) as error:
        log.warning("%s: the kept response is unusable (%s)", file.url, error)
        return None


def rank(month: Month, file: MonthFile) -> tuple[int, int]:
    """How a response compares with another for its month: first the quarter-hours its values
    cover, all measures together, then how final its version is."""
    covered = sum(
        len(month.values[measure.name]) * step_minutes(measure, file.kind) // 15
        for measure in file.area.measures
    )
    return covered, -1 if month.version is None else VERSIONS.index(month.version)


def settled(month: Month, file: MonthFile, best: str, now: datetime) -> bool:
    """Whether a kept month can no longer change, so that a run can skip it: it ended more than
    SETTLED_AFTER ago, it has the most final version ODRÉ announces, and it is whole."""
    if now < file.end + SETTLED_AFTER:
        return False
    if month.version is not None and VERSIONS.index(month.version) < VERSIONS.index(best):
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


def parse_coverage(
    content: bytes, url: str, kind: str, start: datetime
) -> dict[str, tuple[datetime, datetime]]:
    """The first and last instants of each version in a dataset, checked before any use.

    The definitive data must reach back to the start of the history, and the consolidated data
    follow them without a hole; the real-time dataset must have its period.
    """
    data = load_json(content, url)
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise SchemaError(f"{url}: no list of results")
    allowed = ("real_time",) if kind == TR else ("consolidated", "definitive")
    periods = {}
    for row in rows:
        nature = row.get("nature") if isinstance(row, dict) else None
        version = NATURES.get(nature) if isinstance(nature, str) else None
        if version is None or version not in allowed:
            raise SchemaError(f"{url}: unexpected period {row!r}")
        periods[version] = (
            instant(row.get("first"), url, "first"),
            instant(row.get("last"), url, "last"),
        )
    if kind == TR:
        if "real_time" not in periods:
            raise SchemaError(f"{url}: no period of real time")
        return periods
    definitive = periods.get("definitive")
    if definitive is None or definitive[0] > start:
        raise SchemaError(f"{url}: no definitive data back to {start:%Y-%m-%d %H:%M} UTC")
    consolidated = periods.get("consolidated")
    if consolidated is not None and consolidated[0] - definitive[1] > HALF_HOUR:
        raise SchemaError(f"{url}: the consolidated data do not follow the definitive ones")
    return periods


def parse_month(content: bytes, file: MonthFile) -> Month:
    """The values of a monthly export, after a check of the shape of every row."""
    url = file.url
    rows = load_json(content, url)
    if not isinstance(rows, list):
        raise SchemaError(f"{url}: not a list of rows")
    natures = ("real_time",) if file.kind == TR else ("consolidated", "definitive")
    month = Month({measure.name: [] for measure in file.area.measures})
    # Per measure: its field, where its values go, and the minutes its instants must divide.
    measures = [
        (measure.field, month.values[measure.name], step_minutes(measure, file.kind))
        for measure in file.area.measures
    ]
    seen: set[datetime] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SchemaError(f"{url}: a row that is not an object: {row!r}")
        if file.area.region is not None and row.get("code_insee_region") != file.area.region:
            raise SchemaError(f"{url}: a row of region {row.get('code_insee_region')!r}")
        start = instant(row.get("date_heure"), url, "date_heure")
        if start.minute % 15 or start.second or start.microsecond:
            raise SchemaError(f"{url}: {start:%Y-%m-%d %H:%M:%S} UTC is off the quarter-hour grid")
        if not file.start <= start < file.end:
            raise SchemaError(f"{url}: a row of {start:%Y-%m-%d %H:%M} UTC, outside the month")
        # When the clocks go forward, ODRÉ also gives 02:00 to 02:45, which never happened, the
        # instants of 03:00 to 03:45: the same measures, and a forecast only repeated.
        if phantom(row):
            continue
        if start in seen:
            raise SchemaError(f"{url}: two rows for {start:%Y-%m-%d %H:%M} UTC")
        seen.add(start)
        nature = row.get("nature")
        version = NATURES.get(nature) if isinstance(nature, str) else None
        if version is None or version not in natures:
            raise SchemaError(f"{url}: unexpected nature {nature!r} in {file.dataset}")
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
            try:
                values.append((start, float(value), version))
            except OverflowError as error:
                raise SchemaError(f"{url}: unexpected {name} at {start} UTC ({error})") from error
    return month


def phantom(row: dict[str, Any]) -> bool:
    """Whether a row stands for a Paris hour that never happened: only a 02:xx can."""
    hour = row.get("heure")
    if not (isinstance(hour, str) and hour.startswith("02:")):
        return False
    try:
        return never_happened(datetime.strptime(f"{row.get('date')} {hour}", "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError):
        return False


def build(plan: Iterable[tuple[MonthFile, Month, datetime]]) -> pl.DataFrame:
    """The clean measures, month by month from the responses a run has chosen.

    A half-hour value covers its two quarter-hours; an absent value stays absent.
    """
    frames = [values_frame(month, file, received_at) for file, month, received_at in plan]
    values = pl.concat(frames) if frames else SCHEMA.to_frame()
    return (
        values.with_columns(quarter=pl.int_ranges(0, pl.col("step_minutes").cast(pl.Int64) // 15))
        .explode("quarter", empty_as_null=False)
        .with_columns(pl.col("start") + pl.duration(minutes=pl.col("quarter") * 15))
        .drop("quarter")
        .sort("area", "measure", "start")
    )


def values_frame(month: Month, file: MonthFile, received_at: datetime) -> pl.DataFrame:
    """The values of one monthly response, one row per measure and instant, at their published
    step. The columns are built as lists, and the instants as whole microseconds, which Polars
    takes far faster than Python datetimes."""
    starts: list[int] = []
    names: list[str] = []
    numbers: list[float] = []
    steps: list[int] = []
    versions: list[str] = []
    for measure in file.area.measures:
        step = step_minutes(measure, file.kind)
        for start, value, version in month.values[measure.name]:
            starts.append(int(start.timestamp()) * 1_000_000)
            names.append(measure.name)
            numbers.append(value)
            steps.append(step)
            versions.append(version)
    frame = pl.DataFrame(
        {
            "start": starts,
            "measure": names,
            "value": numbers,
            "step_minutes": steps,
            "version": versions,
        },
        schema={
            "start": pl.Int64,
            "measure": SCHEMA["measure"],
            "value": pl.Float64,
            "step_minutes": pl.UInt8,
            "version": SCHEMA["version"],
        },
    )
    return frame.with_columns(
        pl.col("start").cast(SCHEMA["start"]),
        pl.lit(file.area.name, dtype=SCHEMA["area"]).alias("area"),
        pl.lit(received_at, dtype=SCHEMA["received_at"]).alias("received_at"),
    ).select(SCHEMA.names())


def step_minutes(measure: Measure, kind: str) -> int:
    return 15 if kind == TR else measure.settled_step // timedelta(minutes=1)


def check(measures: pl.DataFrame, store: RawStore, *, since: date, now: datetime) -> Report:
    """Invalid rows; errors for each measure up to the day before; warnings for late data.

    The day before is checked from 03:00, once its real time, about an hour late, has come; today
    is not checked.
    """
    report = Report(invalid=invalid_rows(measures, now=now))
    days = list(every_day(since, paris_day(now - STALE_AFTER) - timedelta(days=1)))
    # ODRÉ never gives the first pass of the hour lived twice: it is not counted, and seeing it
    # means ODRÉ has changed.
    passes = first_passes(days)
    report.warnings.extend(
        f"{day}: ODRÉ now gives the first pass of 02:00, not counted"
        for day in sorted({paris_day(start) for start in measures.filter(passes)["start"]})
    )
    counts = {
        (area, name, day): count
        for area, name, day, count in instants_per_day(
            measures.filter(~passes), by=["area", "measure"]
        ).rows()
    }
    expected = {day: len(published(*day_bounds(day), QUARTER_HOUR)) for day in days}
    for area in AREAS:
        for measure in area.measures:
            for day, count in expected.items():
                found = counts.get((area.name, measure.name, day), 0)
                if found < count:
                    report.errors.append(
                        f"{area.name} {measure.name} {day}: {count - found} of {count} "
                        "quarter-hours missing"
                    )
    report.warnings.extend(late_real_time(measures, now))
    report.warnings.extend(late_forecast(measures, now))
    report.errors.extend(f"raw layer: {problem}" for problem in store.verify(SOURCE))
    return report


def first_passes(days: Iterable[date]) -> pl.Expr:
    """Whether a row stands in the first pass of the hour lived twice, on the days checked."""
    instants = [
        start
        for day in days
        if quarter_hours(day) == 100
        for start in (day_bounds(day)[0] + i * QUARTER_HOUR for i in range(100))
        if first_pass(start)
    ]
    return pl.col("start").is_in(pl.Series(instants, dtype=SCHEMA["start"]).implode())


def late_real_time(measures: pl.DataFrame, now: datetime) -> list[str]:
    """A warning for each zone whose last real-time value ended more than STALE_AFTER ago."""
    warnings = []
    for area in AREAS:
        latest = measures.filter(
            (pl.col("area") == area.name)
            & (pl.col("measure") == area.watched)
            & (pl.col("version") == "real_time")
        )["start"].max()
        if not isinstance(latest, datetime):
            warnings.append(f"real time: no {area.name} {area.watched}")
        elif now - (latest + QUARTER_HOUR) > STALE_AFTER:
            warnings.append(
                f"real time: the last {area.name} {area.watched} ends at "
                f"{latest + QUARTER_HOUR:%Y-%m-%d %H:%M} UTC"
            )
    return warnings


def late_forecast(measures: pl.DataFrame, now: datetime) -> list[str]:
    """A warning when, in the afternoon, RTE's forecast of tomorrow is not whole."""
    if now.astimezone(PARIS).hour < PUBLISHED_BY:
        return []
    tomorrow = paris_day(now) + timedelta(days=1)
    start, end = day_bounds(tomorrow)
    found = measures.filter(
        (pl.col("measure") == FORECAST) & (pl.col("start") >= start) & (pl.col("start") < end)
    ).height
    expected = len(published(start, end, QUARTER_HOUR))
    if found >= expected:
        return []
    return [f"{tomorrow}: {found} of {expected} quarter-hours of RTE's forecast published so far"]


def invalid_rows(measures: pl.DataFrame, *, now: datetime) -> list[str]:
    """The rows the clean file must not hold: a quarter-hour twice, an instant off the grid, a
    value outside the limits of its measure, a measure dated after the run, or RTE's forecast
    dated after tomorrow."""
    key = ["area", "measure", "start"]
    duplicates = measures.group_by(key).len().filter(pl.col("len") > 1).sort(key)
    invalid = [
        f"{start:%Y-%m-%d %H:%M} UTC: {area} {name} {count} values"
        for area, name, start, count in duplicates.rows()
    ]
    off_grid = measures.filter(pl.col("start").dt.truncate("15m") != pl.col("start"))
    invalid.extend(
        f"{start:%Y-%m-%d %H:%M:%S} UTC: {area} {name} not on a quarter-hour"
        for area, name, start in off_grid.select(key).rows()
    )
    after_tomorrow = day_bounds(paris_day(now) + timedelta(days=1))[1]
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
            if moment.utcoffset() is not None:
                return moment.astimezone(UTC)
        except (ValueError, OverflowError):
            pass
    raise SchemaError(f"{url}: unexpected {name} {value!r}")
