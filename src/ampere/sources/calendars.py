"""Calendars: the public holidays of mainland France and the school holidays of Lyon (ADR 027).

Each run asks for both calendars whole: the public holidays from Etalab's API, as JSON, and the
school calendar of the ministry of Education, as a Parquet export of the whole dataset. It keeps
each response that changes in the raw layer, rebuilds clean/calendars/days.parquet, one row per
Paris day, from the last readable response of each, and checks it.
"""

import io
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx2
import polars as pl

from ampere.data.clean import Report, write_parquet
from ampere.data.days import day_bounds, every_day, paris_day
from ampere.data.raw import DamagedRawFile, RawStore, Receipt
from ampere.sources.archive import JSON, fetch
from ampere.sources.shapes import SchemaError, load_json, parquet_footer

log = logging.getLogger(__name__)

SOURCE = "calendars"
HOLIDAYS = "public-holidays"
SCHOOL = "school-holidays"
HOLIDAYS_URL = "https://calendrier.api.gouv.fr/jours-feries/metropole.json"
SCHOOL_URL = (
    "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/"
    "fr-en-calendrier-scolaire/exports/parquet"
)
PARQUET = "application/parquet"
# The start of the history, shared by every source.
SINCE = date(2023, 7, 1)
# Seconds between the two requests, out of politeness.
PAUSE = 0.5
MAX_BYTES = 1_000_000
# The school calendar of all of France has 2,600 rows in 7 columns: Parquet compresses by itself,
# and a few kilobytes could unfold into millions of rows.
MAX_ROWS = 10_000
MAX_COLUMNS = 20
ACADEMY = "Lyon"
# The rows for everyone, and those for the pupils when the teachers have their own.
PUPILS = ("-", "Élèves")
# The holidays of every school year, the bridge of Ascension aside.
EVERY_YEAR = (
    "Vacances de la Toussaint",
    "Vacances de Noël",
    "Vacances d'Hiver",
    "Vacances de Printemps",
    "Vacances d'Été",
)
# The ministry publishes its calendar years ahead: one that ends within a year lacks the next.
AHEAD = timedelta(days=365)
SCHEMA = pl.Schema(
    {
        "day": pl.Date(),
        "public_holiday": pl.String(),
        "school_holidays": pl.String(),
        "public_holiday_received_at": pl.Datetime("us", "UTC"),
        "school_holidays_received_at": pl.Datetime("us", "UTC"),
    }
)


@dataclass(frozen=True)
class Period:
    """School holidays of Lyon: their name, their school year, and their first and last days."""

    name: str
    year: str
    first: date
    last: date


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
    """Fetch both calendars, then rebuild the days from the last readable response of each, and
    check them. Both are asked for whole at each run: full changes nothing."""
    kept = len(store.receipts(SOURCE))
    report = Report()

    def ask(dataset: str, request: str, url: str, content_type: str, extension: str) -> None:
        fetch(
            http,
            store,
            source=SOURCE,
            dataset=dataset,
            url=url,
            content_type=content_type,
            extension=extension,
            max_bytes=MAX_BYTES,
            sleep=sleep,
            request=request,
        )

    ask(HOLIDAYS, "metropole", HOLIDAYS_URL, JSON, "json")
    sleep(PAUSE)
    ask(SCHOOL, "fr-en-calendrier-scolaire", SCHOOL_URL, PARQUET, "parquet")
    holidays = latest(store, HOLIDAYS, parse_holidays, report)
    school = latest(store, SCHOOL, parse_school, report)
    if holidays is not None and school is not None:
        frame = build(holidays, school, since=since)
        found = check(frame, school[0], since=since, now=now)
        report.errors.extend(found.errors)
        report.warnings.extend(found.warnings)
        write_parquet(frame, clean / SOURCE / "days.parquet")
    report.errors.extend(f"raw layer: {problem}" for problem in store.verify(SOURCE))
    log.info("%s: 2 requests, %d new responses", SOURCE, len(store.receipts(SOURCE)) - kept)
    return report


def latest[T](
    store: RawStore, dataset: str, parse: Callable[[bytes, str], T], report: Report
) -> tuple[T, Receipt] | None:
    """What the last readable response of a calendar says, with its receipt. The last response
    that does not read is an error; an older one is only logged."""
    receipts = [receipt for receipt in store.receipts(SOURCE) if receipt.dataset == dataset]
    for receipt in reversed(receipts):
        try:
            return parse(store.read(receipt), receipt.url), receipt
        except (DamagedRawFile, SchemaError) as error:
            if receipt == receipts[-1]:
                report.errors.append(str(error))
            else:
                log.warning(
                    "%s: the response received at %s does not read (%s)",
                    dataset,
                    receipt.received_at,
                    error,
                )
    report.errors.append(f"{dataset}: no readable response")
    return None


def parse_holidays(content: bytes, url: str) -> dict[date, str]:
    """The public holidays of mainland France by day, from an object of dates and names."""
    body = load_json(content, url)
    if not isinstance(body, dict) or not body:
        raise SchemaError(f"{url}: not an object of public holidays")
    holidays = {}
    for key, name in body.items():
        try:
            day = date.fromisoformat(key)
        except ValueError:
            day = None
        if day is None or day.isoformat() != key or not isinstance(name, str) or not name.strip():
            raise SchemaError(f"{url}: an unexpected public holiday, {key!r}: {name!r}")
        holidays[day] = name
    return holidays


def parse_school(content: bytes, url: str) -> list[Period]:
    """The school holidays of the pupils of Lyon, in order, from the Parquet export of the whole
    school calendar, after a check of its footer, its columns and its rows."""
    rows, schema = parquet_footer(content, url)
    if rows > MAX_ROWS or len(schema) > MAX_COLUMNS:
        raise SchemaError(
            f"{url}: {rows} rows in {len(schema)} columns, more than the school calendar holds"
        )
    wanted: dict[str, Callable[[pl.DataType], bool]] = {
        **dict.fromkeys(
            ("description", "population", "location", "annee_scolaire"),
            lambda dtype: dtype == pl.String,
        ),
        **dict.fromkeys(
            ("start_date", "end_date"),
            lambda dtype: isinstance(dtype, pl.Datetime) and dtype.time_zone == "UTC",
        ),
    }
    for column, fits in wanted.items():
        dtype = schema.get(column)
        if dtype is None or not fits(dtype):
            raise SchemaError(f"{url}: no column {column} of the expected type, but {dtype}")
    try:
        raw = pl.read_parquet(io.BytesIO(content), columns=list(wanted))
        lyon = raw.filter(
            (pl.col("location") == ACADEMY)
            & pl.col("population").is_in(pl.Series(PUPILS).implode())
        ).sort("start_date")
    except (pl.exceptions.PolarsError, pl.exceptions.PanicException, OSError) as error:
        raise SchemaError(f"{url}: not a Parquet export ({error})") from error
    if lyon.is_empty():
        raise SchemaError(f"{url}: no school holidays for {ACADEMY}")
    for column in wanted:
        if lyon[column].null_count():
            raise SchemaError(f"{url}: a period of {ACADEMY} without its {column}")
    periods: list[Period] = []
    for name, year, start, end in lyon.select(
        "description", "annee_scolaire", "start_date", "end_date"
    ).rows():
        periods.append(period(name, year, start, end, url, periods[-1] if periods else None))
    return periods


def period(
    name: str, year: str, start: datetime, end: datetime, url: str, before: Period | None
) -> Period:
    """A period of school holidays, checked: from its first day without classes to the day
    before they resume, or its first day alone when it starts and ends at the same time."""
    if not re.fullmatch(r"\d{4}-\d{4}", year) or int(year[5:]) != int(year[:4]) + 1:
        raise SchemaError(f"{url}: an unexpected school year, {year!r}")
    first, resume = paris_day(start), paris_day(end)
    if day_bounds(first)[0] != start or day_bounds(resume)[0] != end:
        raise SchemaError(f"{url}: {name} {year} does not start and end at midnight in Paris")
    if end < start:
        raise SchemaError(f"{url}: {name} {year} ends before it starts")
    last = first if end == start else resume - timedelta(days=1)
    if before is not None and first <= before.last:
        raise SchemaError(f"{url}: {name} {year} overlaps {before.name}")
    return Period(name, year, first, last)


def build(
    holidays: tuple[dict[date, str], Receipt],
    school: tuple[list[Period], Receipt],
    *,
    since: date,
) -> pl.DataFrame:
    """One row per Paris day, from since to the last day both calendars know: the 31 December of
    the last year of public holidays, or the last day of the last school holidays if earlier."""
    (names, holidays_receipt), (periods, school_receipt) = holidays, school
    last = min(date(max(names).year, 12, 31), max(period.last for period in periods))
    school_days = {
        day: period.name for period in periods for day in every_day(period.first, period.last)
    }
    days = list(every_day(since, last))
    return pl.DataFrame(
        {
            "day": days,
            "public_holiday": [names.get(day) for day in days],
            "school_holidays": [school_days.get(day) for day in days],
            "public_holiday_received_at": [holidays_receipt.received_at] * len(days),
            "school_holidays_received_at": [school_receipt.received_at] * len(days),
        },
        schema=SCHEMA,
    )


def check(frame: pl.DataFrame, periods: list[Period], *, since: date, now: datetime) -> Report:
    """An error when the days end before tomorrow, the day the forecast covers; warnings when the
    school calendar ends within a year, and for a school year, but the last one, without its
    usual holidays."""
    report = Report()
    today = paris_day(now)
    tomorrow = today + timedelta(days=1)
    last = frame["day"].max()
    if not isinstance(last, date):
        report.errors.append(f"{SOURCE}: no day known since {since}")
    elif last < tomorrow:
        report.errors.append(f"{SOURCE}: the days end on {last}, before tomorrow, {tomorrow}")
    end = max(period.last for period in periods)
    if end - today < AHEAD:
        report.warnings.append(
            f"{SOURCE}: the school calendar of {ACADEMY} ends on {end}, less than a year ahead"
        )
    # The last school year may be published in part only.
    years = sorted({p.year for p in periods if date(int(p.year[:4]), 9, 1) >= since})
    for year in years[:-1]:
        names = {p.name for p in periods if p.year == year}
        missing = [name for name in EVERY_YEAR if name not in names]
        if missing:
            report.warnings.append(
                f"{SOURCE}: the school year {year} of {ACADEMY} has no {', '.join(missing)}"
            )
    return report
