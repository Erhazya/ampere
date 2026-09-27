"""SMARD: the day-ahead price of the France zone, from the Bundesnetzagentur (ADR 003, ADR 023).

SMARD publishes one file per week and per market step. A run asks for the weekly index, then for
the weeks that can still change, up to 14 days after their end, and for those the raw layer does
not hold whole. It keeps every response in the raw layer, rebuilds clean/smard/prices.parquet from
the raw layer alone, and checks the result: missing quarter-hours are reported, never filled.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx2
import polars as pl

from ampere.data.clean import Report, quarter_hours_per_day, write_parquet
from ampere.data.days import PARIS, QUARTER_HOUR, day_bounds, every_day, paris_day, quarter_hours
from ampere.data.http import get
from ampere.data.raw import DamagedRawFile, RawStore
from ampere.sources.shapes import SchemaError, is_int, is_number, load_json

log = logging.getLogger(__name__)

SOURCE = "smard"
# Filter 254, "Marktpreis: Frankreich", in the chart data of SMARD's German site.
BASE = "https://www.smard.de/app/chart_data/254/DE"
JSON = "application/json"
HOUR, QUARTER = "hour", "quarterhour"
# The start of the history: the first day of the Enedis window, shared by every source.
SINCE = date(2023, 7, 1)
# 1 October 2025 at midnight in Paris: the market's first day of prices by the quarter-hour.
QUARTER_HOURS_FROM = datetime(2025, 9, 30, 22, tzinfo=UTC)
# The limits of the European day-ahead market (-500 and 4,000 €/MWh), with some room.
LOWEST, HIGHEST = -500.0, 5000.0
# A week can change until this long after its end: SMARD regenerates some files days later.
SETTLED_AFTER = timedelta(days=14)
# Seconds between two weekly files, out of politeness.
PAUSE = 0.5
# The largest response accepted: SMARD's weekly files weigh about 15 kB, its index 9 kB.
MAX_BYTES = 1_000_000
# After this hour in Paris, tomorrow's prices should be out.
PUBLISHED_BY = 14

SCHEMA = pl.Schema(
    {
        "start": pl.Datetime("us", "UTC"),
        "price_eur_per_mwh": pl.Float64(),
        "market_step_minutes": pl.UInt8(),
        "received_at": pl.Datetime("us", "UTC"),
    }
)


def index_url() -> str:
    return f"{BASE}/index_{QUARTER}.json"


def week_url(resolution: str, week: int) -> str:
    return f"{BASE}/254_DE_{resolution}_{week}.json"


def dataset(resolution: str) -> str:
    return f"prices-{resolution}"


def span(resolution: str, start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """The part of a week that a file gives: hourly prices before the quarter-hour market,
    quarter-hour prices after."""
    if resolution == HOUR:
        return start, min(end, QUARTER_HOURS_FROM)
    return max(start, QUARTER_HOURS_FROM), end


def resolutions(start: datetime, end: datetime) -> list[str]:
    """The files a week needs: those that give part of it."""
    needed = []
    for resolution in (HOUR, QUARTER):
        lower, upper = span(resolution, start, end)
        if lower < upper:
            needed.append(resolution)
    return needed


def week_end(start: datetime) -> datetime:
    """The end of a SMARD week: the next Monday at midnight in Paris."""
    return day_bounds(paris_day(start) + timedelta(days=7))[0]


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
    """Fetch the weeks that can still change or that the raw layer lacks, every week if full,
    then rebuild the clean prices, check them, and replace the clean file if its rows are valid."""
    kept = len(store.receipts(SOURCE))
    weeks = parse_index(fetch(http, store, "index", index_url(), sleep), index_url())
    first = day_bounds(since)[0]
    # Each week ends where the next one starts; the last one, on the next Monday in Paris.
    starts = [moment(week) for week in weeks]
    ends = [*starts[1:], week_end(starts[-1])]
    asked = 0
    for week, start, end in zip(weeks, starts, ends, strict=True):
        if end <= first:
            continue
        for resolution in resolutions(start, end):
            url = week_url(resolution, week)
            if not full and settled(store, resolution, url, start, end, now):
                continue
            if asked:
                sleep(PAUSE)
            asked += 1
            parse_series(fetch(http, store, dataset(resolution), url, sleep), url)
    prices = build(store, since=since)
    log.info(
        "smard: %d weekly files asked, %d new responses, %d quarter-hours",
        asked,
        len(store.receipts(SOURCE)) - kept,
        prices.height,
    )
    report = check(prices, store, since=since, now=now)
    path = clean / SOURCE / "prices.parquet"
    if report.invalid:
        log.error("%s kept as it was: the new prices break its rules", path)
    else:
        write_parquet(prices, path)
    return report


def settled(
    store: RawStore, resolution: str, url: str, start: datetime, end: datetime, now: datetime
) -> bool:
    """Whether the raw layer holds a weekly file for good, so that a run can skip it.

    The week must have ended more than SETTLED_AFTER ago, and its last response must read and give
    every price of its span. A response that is damaged, of an unknown shape or short of prices is
    asked for again, however old the week: after an outage, the run completes what it had.
    """
    if now < end + SETTLED_AFTER:
        return False
    last = store.last(SOURCE, dataset(resolution), url)
    if last is None:
        return False
    try:
        points = parse_series(store.read(last), url)
    except (DamagedRawFile, SchemaError) as error:
        log.warning("%s: the kept response is unusable, asked again (%s)", url, error)
        return False
    lower, upper = span(resolution, start, end)
    step = timedelta(hours=1) if resolution == HOUR else QUARTER_HOUR
    expected = {lower + i * step for i in range((upper - lower) // step)}
    if not expected <= {moment(point) for point, price in points if price is not None}:
        log.warning("%s: the kept response lacks prices, asked again", url)
        return False
    return True


def fetch(
    http: httpx2.Client, store: RawStore, name: str, url: str, sleep: Callable[[float], None]
) -> bytes:
    """GET a SMARD file and keep it in the raw layer, before anything reads it."""
    fetched = get(http, url, content_type=JSON, max_bytes=MAX_BYTES, sleep=sleep)
    saved = store.save(
        source=SOURCE,
        dataset=name,
        request=url,
        url=fetched.url,
        content_type=fetched.content_type,
        content=fetched.content,
        extension="json",
    )
    if saved.new:
        log.info("%s: new response kept in %s", url, saved.receipt.path)
    return fetched.content


def parse_index(content: bytes, url: str) -> list[int]:
    """The starts of the weeks, in milliseconds since 1970, oldest first."""
    data = load_json(content, url)
    weeks = data.get("timestamps") if isinstance(data, dict) else None
    if not isinstance(weeks, list) or not weeks or not all(is_int(week) for week in weeks):
        raise SchemaError(f"{url}: no list of week starts")
    return sorted(weeks)


def parse_series(content: bytes, url: str) -> list[tuple[int, float | None]]:
    """The points of a weekly file: (start in milliseconds, price in €/MWh, or None if absent)."""
    data = load_json(content, url)
    meta = data.get("meta_data") if isinstance(data, dict) else None
    if not isinstance(meta, dict) or meta.get("version") != 1:
        raise SchemaError(f"{url}: meta_data version is not 1")
    series = data.get("series")
    if not isinstance(series, list):
        raise SchemaError(f"{url}: no series")
    points = []
    for point in series:
        if not (isinstance(point, list) and len(point) == 2 and is_int(point[0])):
            raise SchemaError(f"{url}: unexpected point {point!r}")
        price = point[1]
        if price is not None and not (is_number(price)):
            raise SchemaError(f"{url}: unexpected price {point!r}")
        points.append((point[0], None if price is None else float(price)))
    return points


def build(store: RawStore, *, since: date) -> pl.DataFrame:
    """The clean prices, rebuilt from the last response of each weekly file in the raw layer.

    An hourly price covers its four quarter-hours; each price comes from the file of the market
    step of its time. An absent price stays absent.
    """
    first = day_bounds(since)[0]
    rows: list[tuple[datetime, float, int, datetime]] = []
    for resolution, step in ((HOUR, 60), (QUARTER, 15)):
        for receipt in store.latest(SOURCE, dataset(resolution)):
            for start_ms, price in parse_series(store.read(receipt), receipt.request):
                start = moment(start_ms)
                if price is None or (start < QUARTER_HOURS_FROM) != (resolution == HOUR):
                    continue
                quarters = [start + i * QUARTER_HOUR for i in range(step // 15)]
                rows.extend(
                    (quarter, price, step, receipt.received_at)
                    for quarter in quarters
                    if quarter >= first
                )
    return pl.DataFrame(rows, schema=SCHEMA, orient="row").sort("start")


def check(prices: pl.DataFrame, store: RawStore, *, since: date, now: datetime) -> Report:
    """Invalid rows; errors for the past and today; a warning when tomorrow's prices are late."""
    today = paris_day(now)
    tomorrow = today + timedelta(days=1)
    report = Report(invalid=invalid_rows(prices, until=day_bounds(tomorrow)[1]))
    counts = dict(quarter_hours_per_day(prices).rows())
    for day in every_day(since, today):
        expected, found = quarter_hours(day), counts.get(day, 0)
        if found < expected:
            report.errors.append(f"{day}: {expected - found} of {expected} quarter-hours missing")
    if now.astimezone(PARIS).hour >= PUBLISHED_BY and counts.get(tomorrow, 0) < quarter_hours(
        tomorrow
    ):
        report.warnings.append(
            f"{tomorrow}: {counts.get(tomorrow, 0)} of {quarter_hours(tomorrow)} quarter-hours "
            "published so far"
        )
    report.errors.extend(f"raw layer: {problem}" for problem in store.verify(SOURCE))
    return report


def invalid_rows(prices: pl.DataFrame, *, until: datetime) -> list[str]:
    """The rows the clean file must not hold: a quarter-hour twice, an instant off the grid, a
    price outside the market limits, or a price from `until` on, which the market has not set."""
    duplicates = prices.group_by("start").len().filter(pl.col("len") > 1).sort("start")
    invalid = [f"{start:%Y-%m-%d %H:%M} UTC: {count} prices" for start, count in duplicates.rows()]
    off_grid = prices.filter(pl.col("start").dt.truncate("15m") != pl.col("start"))
    invalid.extend(
        f"{start:%Y-%m-%d %H:%M:%S} UTC: not on a quarter-hour" for start in off_grid["start"]
    )
    outside = prices.filter(~pl.col("price_eur_per_mwh").is_between(LOWEST, HIGHEST))
    invalid.extend(
        f"{start:%Y-%m-%d %H:%M} UTC: {price} €/MWh, outside {LOWEST:.0f} to {HIGHEST:.0f}"
        for start, price in outside.select("start", "price_eur_per_mwh").rows()
    )
    ahead = prices.filter(pl.col("start") >= until)
    invalid.extend(
        f"{start:%Y-%m-%d %H:%M} UTC: a price after tomorrow" for start in ahead["start"]
    )
    return invalid


def moment(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, UTC)
