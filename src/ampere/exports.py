"""The export of the Data screen (ADR 029): the recent days of each series, written after each
daily run into one JSON file, exports/recent.json, that the API serves as it is."""

import json
import logging
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from ampere.data.clean import Report
from ampere.data.days import day_bounds, every_day, paris_day
from ampere.data.raw import write_atomically
from ampere.recent import EXPORT, LABEL, SERIES, SOURCES, Recent

log = logging.getLogger(__name__)

# The period: the seven Paris days before the day of the export, that day, and the next one.
DAYS_BEFORE = 7
# The measures of éCO2mix on the screen: the series, the area and the measure.
ECO2MIX = (
    ("consumption", "FR", "consumption_mw"),
    ("rte_forecast", "FR", "rte_forecast_mw"),
    ("co2", "FR", "co2_g_per_kwh"),
    ("solar", "ARA", "solar_mw"),
)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def period(now: datetime) -> tuple[datetime, datetime]:
    """From Paris midnight seven days before the Paris day of `now`, to the end of the next day."""
    today = paris_day(now)
    return (
        day_bounds(today - timedelta(days=DAYS_BEFORE))[0],
        day_bounds(today + timedelta(days=1))[1],
    )


def write_export(root: Path, now: datetime) -> Report:
    """Write the export from the clean tables of a data folder, and report what it lacks. A table
    that is missing leaves its series empty, with a warning; one that does not read, with an
    error. An export that the API would refuse is not written: the one before stays."""
    report = Report()
    content = build(root / "clean", now, report)
    try:
        # First with the series named, then as the API reads the file: strict, from JSON.
        Recent.model_validate(content)
        data = json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        Recent.model_validate_json(data, strict=True)
    except ValidationError as error:
        report.errors.append(f"the export is not written: {refused(error, content)}")
        return report
    write_atomically(root / EXPORT, data.encode())
    log.info("export: %s, %d bytes", root / EXPORT, len(data))
    return report


def build(clean: Path, now: datetime, report: Report) -> dict[str, Any]:
    """The content of the export, from the clean tables."""
    start, end = period(now)
    received: dict[str, datetime | None] = {}
    series = []

    prices = within(table(clean / "smard" / "prices.parquet", report), "start", start, end)
    series.append(entry("price", prices, "start", "price_eur_per_mwh"))
    received["smard"] = latest(prices)

    measures = table(clean / "eco2mix" / "measures.parquet", report)
    used = []
    for name, area, measure in ECO2MIX:
        rows = within(
            None
            if measures is None
            else measures.filter((pl.col("area") == area) & (pl.col("measure") == measure)),
            "start",
            start,
            end,
        )
        series.append(entry(name, rows, "start", "value"))
        used.append(rows)
    received["eco2mix"] = latest(*used)

    observed, forecast = temperatures(table(clean / "openmeteo" / "weather.parquet", report))
    observed = within(observed, "time", start, end)
    forecast = within(forecast, "time", start, end)
    series.append(entry("temperature", observed, "time", "value"))
    series.append(entry("temperature_forecast", forecast, "time", "value"))
    received["openmeteo"] = latest(observed, forecast)

    first, last = paris_day(start), paris_day(end - timedelta(microseconds=1))
    calendar = table(clean / "calendars" / "days.parquet", report)
    named = (
        {}
        if calendar is None
        else {
            row["day"]: row
            for row in calendar.filter(pl.col("day").is_between(first, last)).iter_rows(named=True)
        }
    )
    days = [
        {
            "day": day.isoformat(),
            "public_holiday": label(named.get(day, {}).get("public_holiday")),
            "school_holidays": label(named.get(day, {}).get("school_holidays")),
        }
        for day in every_day(first, last)
    ]
    for dataset, column in [
        ("school-holidays", "school_holidays_received_at"),
        ("public-holidays", "public_holiday_received_at"),
    ]:
        received[dataset] = max((row[column] for row in named.values()), default=None)

    updated = update_dates(clean, report)
    eco2mix = [moment for name, moment in updated.items() if name.startswith("eco2mix-")]
    dates = {
        "eco2mix": max(eco2mix, default=None),
        "school-holidays": updated.get("school-holidays"),
        "public-holidays": updated.get("public-holidays"),
    }
    sources = [
        {
            "id": source,
            "name": name,
            "licence": licence,
            "updated_at": millis(dates.get(source)),
            "received_at": millis(received[source]),
        }
        for source, (name, licence) in SOURCES.items()
    ]
    return {
        "generated_at": millis(now),
        "start": millis(start),
        "end": millis(end),
        "series": series,
        "days": days,
        "sources": sources,
    }


def table(path: Path, report: Report) -> pl.DataFrame | None:
    """A clean table; None when it is missing, with a warning, or does not read, with an error."""
    if not path.exists():
        report.warnings.append(f"{path} is missing: its series are left empty")
        return None
    try:
        return pl.read_parquet(path)
    # Polars may even panic on a damaged file, and its panic is a BaseException.
    except (OSError, pl.exceptions.PolarsError, pl.exceptions.PanicException) as error:
        report.errors.append(f"{path} does not read ({error}): its series are left empty")
        return None


def within(frame: pl.DataFrame | None, column: str, start: datetime, end: datetime) -> pl.DataFrame:
    """The rows of a table from start to end, excluded, in the order of their instants."""
    if frame is None:
        return pl.DataFrame({column: [], "value": [], "received_at": []})
    return frame.filter((pl.col(column) >= start) & (pl.col(column) < end)).sort(column)


def temperatures(weather: pl.DataFrame | None) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    """The observed temperature, and the one the latest run forecasts after the last observed
    hour."""
    if weather is None:
        return None, None
    temperature = weather.filter(pl.col("variable") == "temperature_c")
    observed = temperature.filter(pl.col("kind") == "observed")
    forecasts = temperature.filter(pl.col("kind") == "forecast")
    if forecasts.is_empty():
        return observed, forecasts
    forecast = forecasts.filter(pl.col("run") == forecasts["run"].max())
    if not observed.is_empty():
        forecast = forecast.filter(pl.col("time") > observed["time"].max())
    return observed, forecast


def entry(name: str, rows: pl.DataFrame, time: str, value: str) -> dict[str, Any]:
    """A series of the export: its source and unit, and its points as [milliseconds, value]
    pairs."""
    source, unit = SERIES[name]
    points = rows.select(pl.col(time).dt.epoch("ms"), pl.col(value)).rows() if rows.height else []
    return {"id": name, "source": source, "unit": unit, "points": points}


def latest(*frames: pl.DataFrame) -> datetime | None:
    """The latest reception among the rows of these tables."""
    moments = [frame["received_at"].max() for frame in frames if frame.height]
    return max((moment for moment in moments if isinstance(moment, datetime)), default=None)


def update_dates(clean: Path, report: Report) -> dict[str, datetime]:
    """The date each dataset was last updated, as its source published it (ADR 029)."""
    dates: dict[str, datetime] = {}
    for source in ("eco2mix", "calendars"):
        updates = table(clean / source / "updates.parquet", report)
        if updates is not None:
            dates.update(updates.select("dataset", "updated_at").rows())
    return dates


def label(text: str | None) -> str | None:
    """A third-party name as the screen shows it: without control or format characters, such as a
    line break or a change of direction, and at most LABEL characters (ADR 029)."""
    if text is None:
        return None
    kept = " ".join("".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf")).split())
    if not kept:
        return None
    return kept if len(kept) <= LABEL else kept[: LABEL - 1] + "…"


def millis(moment: datetime | None) -> int | None:
    """An instant in milliseconds since 1970, as JavaScript counts them."""
    return None if moment is None else (moment - EPOCH) // timedelta(milliseconds=1)


def refused(error: ValidationError, content: dict[str, Any]) -> str:
    """Where the export breaks its shape, with the series named."""
    places = set()
    for problem in error.errors():
        where = problem["loc"]
        if len(where) > 1 and where[0] == "series" and isinstance(where[1], int):
            places.add(str(content["series"][where[1]]["id"]))
        else:
            places.add(".".join(map(str, where)))
    return f"{error.error_count()} values refused, in {', '.join(sorted(places))}"
