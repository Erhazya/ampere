"""Open-Meteo: the weather of Lyon, as observed and as forecast at the time (ADR 025).

The observed weather comes from the archive, the ECMWF IFS series at 9 km, one UTC month per
request; the forecasts come from the single runs, the 00 UTC run of each day, two days long. A run
of the ingestion fetches the months that can still change, up to 14 days after their end, and the
months and runs the raw layer does not hold whole and readable. It keeps every response in the raw
layer, rebuilds clean/openmeteo/weather.parquet from the last readable response of each month and
each run, and checks it.
"""

import json
import logging
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx2
import polars as pl

from ampere.data.clean import Report, instants_per_day, write_parquet
from ampere.data.days import day_bounds, every_day, paris_day, quarter_hours
from ampere.data.raw import DamagedRawFile, RawStore, Receipt
from ampere.sources.archive import fetch_json
from ampere.sources.shapes import SchemaError, is_int, is_number, load_json

log = logging.getLogger(__name__)

SOURCE = "openmeteo"
OBSERVED, FORECAST = "observed", "forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
RUNS = "https://single-runs-api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs"
# The point of Lyon (conception, section 5), and how far the grid point that answers may lie from
# it, in degrees: ECMWF IFS answers from 45.73° N, 4.83° E.
LATITUDE, LONGITUDE = 45.76, 4.84
NEAR = 0.1
# The start of the history, shared by every source.
SINCE = date(2023, 7, 1)
# The first 00 UTC run of ECMWF IFS that Open-Meteo keeps (ADR 007).
FIRST_RUN = date(2024, 3, 14)
HOUR = timedelta(hours=1)
# A run is asked for two days: 48 hours from its launch.
RUN_HOURS = 48
# A month can change until this long after its end, as with the other sources.
SETTLED_AFTER = timedelta(days=14)
# Seconds between two requests, out of politeness.
PAUSE = 0.5
# The largest response accepted: a month weighs 37 kB.
MAX_BYTES = 1_000_000
# A run comes about 6 h 30 after its launch: from this hour UTC, the run of the day is late.
LATE_FROM = 12


@dataclass(frozen=True)
class Variable:
    """A variable of the weather: its name in the clean table, its field and unit on Open-Meteo,
    its limits, and whether a value is the mean of the hour that ends at its time, as for a
    radiation, rather than the value at that time."""

    name: str
    field: str
    unit: str
    lowest: float
    highest: float
    mean_of_hour: bool = False


VARIABLES = (
    Variable("temperature_c", "temperature_2m", "°C", -40, 50),
    Variable("global_w_m2", "shortwave_radiation", "W/m²", 0, 1500, mean_of_hour=True),
    Variable("direct_horizontal_w_m2", "direct_radiation", "W/m²", 0, 1500, mean_of_hour=True),
    Variable("diffuse_w_m2", "diffuse_radiation", "W/m²", 0, 1500, mean_of_hour=True),
    Variable("direct_normal_w_m2", "direct_normal_irradiance", "W/m²", 0, 1500, mean_of_hour=True),
    Variable("wind_speed_m_s", "wind_speed_10m", "m/s", 0, 75),
)

SCHEMA = pl.Schema(
    {
        "time": pl.Datetime("us", "UTC"),
        "variable": pl.Enum([variable.name for variable in VARIABLES]),
        "value": pl.Float64(),
        "kind": pl.Enum([OBSERVED, FORECAST]),
        "run": pl.Datetime("us", "UTC"),
        "received_at": pl.Datetime("us", "UTC"),
    }
)

# The values of a response, per variable: (time, value) for each hour that has one.
Values = dict[str, list[tuple[datetime, float]]]


@dataclass(frozen=True)
class Request:
    """A month of observed weather or a run: its dataset and its name in the raw layer, the hours
    it may hold, from start to end, and the URL that asks for it."""

    kind: str
    name: str
    start: datetime
    end: datetime
    url: str

    @property
    def run(self) -> datetime | None:
        return self.start if self.kind == FORECAST else None

    def expected(self, variable: Variable) -> set[datetime]:
        """The hours of a variable in a whole response. A run has no mean for the hour before its
        launch."""
        first = self.start + HOUR if self.run is not None and variable.mean_of_hour else self.start
        return {first + i * HOUR for i in range((self.end - first) // HOUR)}


@dataclass(frozen=True)
class Held:
    """A response read from the raw layer: its values, its reception time, and whether it is the
    last response kept for its request, or an older one, after unusable ones."""

    values: Values
    received_at: datetime
    last: bool = True


def midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def months(first: date, today: date) -> list[Request]:
    """The UTC months of observed weather from the one of `first` to the one of today. The first
    starts on `first`; the last is asked up to today, the latest end the archive accepts."""
    requests = []
    month = first.replace(day=1)
    while month <= today:
        following = (month + timedelta(days=31)).replace(day=1)
        start = max(month, first)
        last = min(following - timedelta(days=1), today)
        url = f"{ARCHIVE}?{query(start_date=start.isoformat(), end_date=last.isoformat())}"
        requests.append(
            Request(OBSERVED, f"{month:%Y-%m}", midnight(start), midnight(following), url)
        )
        month = following
    return requests


def runs(first: date, last: date) -> list[Request]:
    """The 00 UTC run of each day from first to last."""
    requests = []
    for day in every_day(first, last):
        launch = midnight(day)
        name = f"{launch:%Y-%m-%dT%H:%M}"
        url = f"{RUNS}?{query(run=name, forecast_days=str(RUN_HOURS // 24))}"
        requests.append(Request(FORECAST, name, launch, launch + RUN_HOURS * HOUR, url))
    return requests


def query(**asked: str) -> str:
    """The query of a request: the point of Lyon, what it asks, the six variables of ECMWF IFS,
    the hours in UTC, and the wind in m/s."""
    return urlencode(
        {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            **asked,
            "hourly": ",".join(variable.field for variable in VARIABLES),
            "models": MODEL,
            "timezone": "GMT",
            "wind_speed_unit": "ms",
        },
        safe=",:",
    )


def ingest(
    http: httpx2.Client,
    store: RawStore,
    clean: Path,
    *,
    now: datetime,
    since: date = SINCE,
    first_run: date = FIRST_RUN,
    full: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Fetch the months that can still change, and the months and runs the raw layer does not
    hold whole and readable, everything if full; then rebuild the clean weather, check it, and
    replace the clean file if its rows are valid."""
    kept = len(store.receipts(SOURCE))
    today = now.astimezone(UTC).date()
    history = responses(store)
    plan: list[tuple[Request, Held]] = []
    errors: list[str] = []
    warnings: list[str] = []
    asked = 0

    def ask(request: Request) -> tuple[bytes, Receipt] | None:
        nonlocal asked
        if asked:
            sleep(PAUSE)
        asked += 1
        try:
            return fetch_json(
                http,
                store,
                source=SOURCE,
                dataset=request.kind,
                url=request.url,
                max_bytes=MAX_BYTES,
                sleep=sleep,
                request=request.name,
                fingerprint=fingerprint,
            )
        except httpx2.HTTPStatusError as error:
            # Open-Meteo answers 400 for a run it does not have, or not yet.
            if request.run is None or error.response.status_code != 400:
                raise
            log.info("%s: no run %s at Open-Meteo (HTTP 400)", SOURCE, request.name)
            return None

    for request in [*months(day_bounds(since)[0].date(), today), *runs(first_run, today)]:
        held = readable(store, history.get((request.kind, request.name), []), request)
        if full or held is None or not held.last or not settled(held, request, now):
            fetched = ask(request)
            if fetched is not None:
                content, receipt = fetched
                try:
                    held = Held(parse(content, request, receipt.url), receipt.received_at)
                except SchemaError as error:
                    # A faulty response is an error; the run goes on with the last readable one.
                    errors.append(str(error))
                    held = None if held is None else replace(held, last=False)
        if held is not None:
            if not held.last:
                warnings.append(
                    f"{request.kind} {request.name}: the response received at "
                    f"{held.received_at:%Y-%m-%d %H:%M:%S} UTC is used, the later ones are "
                    "unusable"
                )
            plan.append((request, held))
    weather = build(plan)
    log.info(
        "%s: %d requests, %d new responses, %d values",
        SOURCE,
        asked,
        len(store.receipts(SOURCE)) - kept,
        weather.height,
    )
    report = check(weather, store, since=since, first_run=first_run, now=now)
    report.errors[:0] = errors
    report.warnings[:0] = warnings
    path = clean / SOURCE / "weather.parquet"
    if report.invalid:
        log.error("%s kept as it was: the new values break its rules", path)
    else:
        write_parquet(weather, path)
    return report


def fingerprint(content: bytes) -> bytes:
    """What must match for two responses to hold the same data: all of it but generationtime_ms,
    which changes at every call, whatever the order of the keys."""
    try:
        body = json.loads(content)
        if isinstance(body, dict):
            body.pop("generationtime_ms", None)
        return json.dumps(body, sort_keys=True).encode()
    except (ValueError, RecursionError):  # not JSON, or nested too deep: every byte counts
        return content


def responses(store: RawStore) -> dict[tuple[str, str], list[Receipt]]:
    """The responses kept for each month and each run, in the order they came."""
    kept: dict[tuple[str, str], list[Receipt]] = {}
    for receipt in store.receipts(SOURCE):
        kept.setdefault((receipt.dataset, receipt.request), []).append(receipt)
    return kept


def readable(store: RawStore, receipts: list[Receipt], request: Request) -> Held | None:
    """The last response kept for a request that still reads; None if there is none. Each later
    one, damaged or of an unknown shape, is logged."""
    for receipt in reversed(receipts):
        try:
            values = parse(store.read(receipt), request, receipt.url)
        except (DamagedRawFile, SchemaError) as error:
            log.warning(
                "%s %s: a kept response is unusable (%s)", request.kind, request.name, error
            )
            continue
        return Held(values, receipt.received_at, last=receipt is receipts[-1])
    return None


def settled(held: Held, request: Request, now: datetime) -> bool:
    """Whether a kept response can no longer change, so that a run can skip its request: a whole
    run, or a whole month that ended more than SETTLED_AFTER ago."""
    if request.run is None and now < request.end + SETTLED_AFTER:
        return False
    if not complete(held.values, request):
        log.warning(
            "%s %s: the kept response lacks values, asked again", request.kind, request.name
        )
        return False
    return True


def complete(values: Values, request: Request) -> bool:
    """Whether a response has a value for every hour of its month or run."""
    return all(
        request.expected(variable) <= {moment for moment, _ in values[variable.name]}
        for variable in VARIABLES
    )


def parse(content: bytes, request: Request, url: str) -> Values:
    """The values of a response, after a check of its shape: an object, from a grid point near
    Lyon, in UTC and in the units asked, whose hours follow one another from the start of its
    month or run, without leaving it."""
    body = load_json(content, url)
    if not isinstance(body, dict):
        raise SchemaError(f"{url}: not an object")
    latitude, longitude = body.get("latitude"), body.get("longitude")
    if not (
        is_number(latitude)
        and is_number(longitude)
        and abs(latitude - LATITUDE) <= NEAR
        and abs(longitude - LONGITUDE) <= NEAR
    ):
        raise SchemaError(f"{url}: a grid point at {latitude!r}, {longitude!r}, not near Lyon")
    offset = body.get("utc_offset_seconds")
    if not (is_int(offset) and offset == 0):
        raise SchemaError(f"{url}: hours with an offset of {offset!r} s, not in UTC")
    units = body.get("hourly_units")
    units = units if isinstance(units, dict) else {}
    for field, unit in [("time", "iso8601"), *((v.field, v.unit) for v in VARIABLES)]:
        if units.get(field) != unit:
            raise SchemaError(f"{url}: {field} in {units.get(field)!r}, not in {unit!r}")
    hourly = body.get("hourly")
    if not (isinstance(hourly, dict) and isinstance(hourly.get("time"), list) and hourly["time"]):
        raise SchemaError(f"{url}: no hours")
    times = hourly["time"]
    hours = (request.end - request.start) // HOUR
    if len(times) > hours:
        raise SchemaError(
            f"{url}: {len(times)} hours, more than the {hours} of {request.kind} {request.name}"
        )
    moments = [request.start + i * HOUR for i in range(len(times))]
    for i, (written, moment) in enumerate(zip(times, moments, strict=True)):
        expected = f"{moment:%Y-%m-%dT%H:%M}"
        if written != expected:
            raise SchemaError(f"{url}: hour {i} is {written!r}, not {expected!r}")
    values: Values = {}
    for variable in VARIABLES:
        series = hourly.get(variable.field)
        if not isinstance(series, list) or len(series) != len(times):
            raise SchemaError(f"{url}: no list of {len(times)} values for {variable.field}")
        found = values[variable.name] = []
        for moment, value in zip(moments, series, strict=True):
            if value is None:
                continue
            number = finite(value)
            if number is None:
                raise SchemaError(
                    f"{url}: unexpected {variable.field} {value!r} at {moment:%Y-%m-%d %H:%M} UTC"
                )
            found.append((moment, number))
    return values


def finite(value: object) -> float | None:
    """A JSON number as a float; None for anything else, or for a number that is not finite."""
    if not is_number(value):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer too large for a float
        return None
    return number if math.isfinite(number) else None


def build(plan: Iterable[tuple[Request, Held]]) -> pl.DataFrame:
    """The clean weather, from the responses a run has chosen."""
    frames = [values_frame(request, held) for request, held in plan]
    weather = pl.concat(frames) if frames else SCHEMA.to_frame()
    return weather.sort("kind", "variable", "run", "time")


def values_frame(request: Request, held: Held) -> pl.DataFrame:
    """The values of one response, one row per variable and hour. An observed value later than
    the reception of its response is left out: the archive fills the rest of the day with the
    forecast. The columns are built as lists, and the times as whole microseconds, which Polars
    takes far faster than Python datetimes."""
    times: list[int] = []
    names: list[str] = []
    numbers: list[float] = []
    for variable in VARIABLES:
        for moment, value in held.values[variable.name]:
            if request.run is None and moment > held.received_at:
                continue
            times.append(int(moment.timestamp()) * 1_000_000)
            names.append(variable.name)
            numbers.append(value)
    frame = pl.DataFrame(
        {"time": times, "variable": names, "value": numbers},
        schema={"time": pl.Int64, "variable": SCHEMA["variable"], "value": pl.Float64},
    )
    return frame.with_columns(
        pl.col("time").cast(SCHEMA["time"]),
        pl.lit(request.kind, dtype=SCHEMA["kind"]).alias("kind"),
        pl.lit(request.run, dtype=SCHEMA["run"]).alias("run"),
        pl.lit(held.received_at, dtype=SCHEMA["received_at"]).alias("received_at"),
    ).select(SCHEMA.names())


def check(
    weather: pl.DataFrame, store: RawStore, *, since: date, first_run: date, now: datetime
) -> Report:
    """Invalid rows; errors for the observed hours up to the day before and for the runs up to
    the one of the day before; a warning when the run of the day is late."""
    report = Report(invalid=invalid_rows(weather))
    report.errors.extend(missing_hours(weather, since, now))
    today = now.astimezone(UTC).date()
    report.errors.extend(missing_runs(weather, runs(first_run, today - timedelta(days=1))))
    if now.astimezone(UTC).hour >= LATE_FROM and today >= first_run:
        report.warnings.extend(
            f"{problem} at {now.astimezone(UTC):%H:%M} UTC"
            for problem in missing_runs(weather, runs(today, today))
        )
    report.errors.extend(f"raw layer: {problem}" for problem in store.verify(SOURCE))
    return report


def missing_hours(weather: pl.DataFrame, since: date, now: datetime) -> list[str]:
    """An error for each variable and Paris day, up to the day before, that lacks observed hours:
    24 in a day, 23 and 25 when the clocks change."""
    observed = weather.filter(pl.col("kind") == OBSERVED)
    counts = {
        (name, day): count
        for name, day, count in instants_per_day(
            observed, by=["variable"], column="time", every="1h"
        ).rows()
    }
    errors = []
    days = list(every_day(since, paris_day(now) - timedelta(days=1)))
    for variable in VARIABLES:
        for day in days:
            expected = quarter_hours(day) // 4
            found = counts.get((variable.name, day), 0)
            if found < expected:
                errors.append(
                    f"observed {variable.name} {day}: {expected - found} of {expected} hours "
                    "missing"
                )
    return errors


def missing_runs(weather: pl.DataFrame, requests: list[Request]) -> list[str]:
    """What each run lacks: all of it, or some hours of a variable."""
    forecasts = weather.filter(
        (pl.col("kind") == FORECAST) & (pl.col("time").dt.truncate("1h") == pl.col("time"))
    )
    counts = {
        (run, name): count
        for run, name, count in forecasts.select("run", "variable", "time")
        .unique()
        .group_by("run", "variable")
        .len()
        .rows()
    }
    problems = []
    for request in requests:
        found = {variable: counts.get((request.start, variable.name), 0) for variable in VARIABLES}
        if not any(found.values()):
            problems.append(f"run {request.name}: missing")
            continue
        for variable, count in found.items():
            expected = len(request.expected(variable))
            if count < expected:
                problems.append(
                    f"run {request.name} {variable.name}: {expected - count} of {expected} hours "
                    "missing"
                )
    return problems


def invalid_rows(weather: pl.DataFrame) -> list[str]:
    """The rows the clean file must not hold: an hour twice for a variable, in the observed
    weather or in one run; an instant off the hour; a value outside the limits of its variable."""

    def origin(run: datetime | None) -> str:
        return "observed" if run is None else f"run {run:%Y-%m-%dT%H:%M}"

    key = ["kind", "run", "variable", "time"]
    duplicates = weather.group_by(key).len().filter(pl.col("len") > 1).sort(key)
    invalid = [
        f"{moment:%Y-%m-%d %H:%M} UTC: {origin(run)} {name} {count} values"
        for _, run, name, moment, count in duplicates.rows()
    ]
    off_hour = weather.filter(pl.col("time").dt.truncate("1h") != pl.col("time")).sort(key)
    invalid.extend(
        f"{moment:%Y-%m-%d %H:%M:%S} UTC: {origin(run)} {name} not on the hour"
        for run, name, moment in off_hour.select("run", "variable", "time").rows()
    )
    for variable in VARIABLES:
        outside = weather.filter(
            (pl.col("variable") == variable.name)
            & ~pl.col("value").is_between(variable.lowest, variable.highest)
        ).sort(key)
        invalid.extend(
            f"{moment:%Y-%m-%d %H:%M} UTC: {origin(run)} {variable.name} {number}, "
            f"outside {variable.lowest:g} to {variable.highest:g}"
            for run, moment, number in outside.select("run", "time", "value").rows()
        )
    return invalid
