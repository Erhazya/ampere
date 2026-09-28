"""The export of the Data screen (ADR 029): the recent days of each series, in one JSON file."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from ampere.data.clean import UPDATES, write_parquet
from ampere.data.days import day_bounds
from ampere.exports import period, write_export
from ampere.recent import EXPORT, LABEL, Recent
from ampere.sources import calendars, eco2mix, openmeteo, smard

# A Monday, just after the daily job of 14:00 in Paris.
NOW = datetime(2026, 9, 28, 12, 5, tzinfo=UTC)
# From Paris midnight on Monday 21 September to the end of Tuesday 29 September.
START = day_bounds(date(2026, 9, 21))[0]
END = day_bounds(date(2026, 9, 29))[1]
RECEIVED = datetime(2026, 9, 28, 12, 1, tzinfo=UTC)
QUARTER = timedelta(minutes=15)
HOUR = timedelta(hours=1)


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def every(first: datetime, last: datetime, step: timedelta) -> list[datetime]:
    """Every step from first to last, excluded."""
    return [first + n * step for n in range((last - first) // step)]


# The clean tables run from two days before the period to two days after it, and real time stops
# an hour before now, as with éCO2mix.
BEFORE, AFTER = START - timedelta(days=2), END + timedelta(days=2)
MEASURED = NOW - HOUR


def measures() -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for moment in every(BEFORE, AFTER, QUARTER):
        hour = moment.hour
        for area, measure, value, ahead in [
            ("FR", "consumption_mw", 40_000.0 + hour, False),
            ("FR", "co2_g_per_kwh", 20.0 + hour, False),
            ("FR", "rte_forecast_mw", 41_000.0 + hour, True),
            ("ARA", "solar_mw", 100.0 * hour, False),
            ("ARA", "solar_load_factor_pct", 1.0, False),
        ]:
            if ahead or moment < MEASURED:
                rows.append(
                    {
                        "start": moment,
                        "area": area,
                        "measure": measure,
                        "value": value,
                        "step_minutes": 15,
                        "version": "real_time",
                        "received_at": RECEIVED,
                    }
                )
    return pl.DataFrame(rows, schema=eco2mix.SCHEMA)


def weather() -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    observed_until = datetime(2026, 9, 28, 10, tzinfo=UTC)
    for moment in every(BEFORE, observed_until, HOUR):
        for variable, value in [("temperature_c", 15.0), ("global_w_m2", 300.0)]:
            rows.append(
                {
                    "time": moment,
                    "variable": variable,
                    "value": value,
                    "kind": "observed",
                    "run": None,
                    "received_at": RECEIVED,
                }
            )
    # Two runs over the same hours: the latest one wins.
    for run, value in [
        (datetime(2026, 9, 27, tzinfo=UTC), 30.0),
        (datetime(2026, 9, 28, tzinfo=UTC), 18.0),
    ]:
        for moment in every(run, run + timedelta(days=3), HOUR):
            rows.append(
                {
                    "time": moment,
                    "variable": "temperature_c",
                    "value": value,
                    "kind": "forecast",
                    "run": run,
                    "received_at": run + timedelta(hours=8),
                }
            )
    return pl.DataFrame(rows, schema=openmeteo.SCHEMA)


def days() -> pl.DataFrame:
    names: dict[date, tuple[str | None, str | None]] = {
        date(2026, 9, 23): ("Jour \x00de‮ test", None),
        date(2026, 9, 25): (None, "É" * 300),
    }
    rows = []
    day = date(2026, 9, 15)
    while day < date(2026, 10, 10):
        holiday, school = names.get(day, (None, None))
        rows.append(
            {
                "day": day,
                "public_holiday": holiday,
                "school_holidays": school,
                "public_holiday_received_at": datetime(2026, 9, 27, 19, tzinfo=UTC),
                "school_holidays_received_at": datetime(2026, 9, 27, 20, tzinfo=UTC),
            }
        )
        day += timedelta(days=1)
    return pl.DataFrame(rows, schema=calendars.SCHEMA)


def updates(rows: list[tuple[str, datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        [(name, updated_at, RECEIVED) for name, updated_at in rows], schema=UPDATES, orient="row"
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A data folder with the clean tables the export reads."""
    clean = tmp_path / "clean"
    instants = every(BEFORE, AFTER, QUARTER)
    prices = pl.DataFrame(
        {
            "start": instants,
            "price_eur_per_mwh": [float(n % 97) for n in range(len(instants))],
            "market_step_minutes": [15] * len(instants),
            "received_at": [RECEIVED] * len(instants),
        },
        schema=smard.SCHEMA,
    )
    write_parquet(prices, clean / "smard" / "prices.parquet")
    write_parquet(measures(), clean / "eco2mix" / "measures.parquet")
    write_parquet(weather(), clean / "openmeteo" / "weather.parquet")
    write_parquet(days(), clean / "calendars" / "days.parquet")
    write_parquet(
        updates(
            [
                ("eco2mix-national-tr", datetime(2026, 9, 28, 11, 45, tzinfo=UTC)),
                ("eco2mix-national-cons-def", datetime(2026, 9, 27, 22, tzinfo=UTC)),
            ]
        ),
        clean / "eco2mix" / "updates.parquet",
    )
    write_parquet(
        updates(
            [
                ("public-holidays", datetime(2026, 1, 2, 14, 32, 45, tzinfo=UTC)),
                ("school-holidays", datetime(2026, 9, 18, 8, 35, tzinfo=UTC)),
            ]
        ),
        clean / "calendars" / "updates.parquet",
    )
    return tmp_path


def export(root: Path, now: datetime = NOW) -> dict[str, Any]:
    report = write_export(root, now)
    assert not report.failed, report
    content: dict[str, Any] = json.loads((root / EXPORT).read_bytes())
    return content


def series(content: dict[str, Any], name: str) -> list[list[float]]:
    (found,) = [item for item in content["series"] if item["id"] == name]
    points: list[list[float]] = found["points"]
    return points


def test_the_period_runs_from_seven_days_before_to_the_end_of_tomorrow() -> None:
    assert period(NOW) == (START, END)
    # Just before midnight in Paris, the Paris day is still Sunday.
    assert period(datetime(2026, 9, 27, 21, 59, tzinfo=UTC)) == (
        day_bounds(date(2026, 9, 20))[0],
        day_bounds(date(2026, 9, 28))[1],
    )
    # Across the change to winter time on 25 October 2026, each bound is a Paris midnight.
    start, end = period(datetime(2026, 10, 26, 12, tzinfo=UTC))
    assert (start, end) == (
        datetime(2026, 10, 18, 22, tzinfo=UTC),
        datetime(2026, 10, 27, 23, tzinfo=UTC),
    )


def test_the_export_holds_the_period_and_passes_the_shape_the_api_checks(root: Path) -> None:
    content = export(root)
    assert (content["generated_at"], content["start"], content["end"]) == (
        ms(NOW),
        ms(START),
        ms(END),
    )
    Recent.model_validate_json((root / EXPORT).read_bytes())


def test_the_price_covers_the_period_up_to_the_end_of_tomorrow(root: Path) -> None:
    points = series(export(root), "price")
    assert len(points) == 9 * 96
    assert (points[0][0], points[-1][0]) == (ms(START), ms(END - QUARTER))
    assert [time for time, _ in points] == sorted(time for time, _ in points)


def test_each_measure_of_eco2mix_goes_to_its_own_series(root: Path) -> None:
    content = export(root)
    consumption, forecast = series(content, "consumption"), series(content, "rte_forecast")
    # Measured up to an hour before now, the last quarter-hour before 11:05; RTE's forecast runs
    # to the end of tomorrow.
    assert consumption[-1][0] == ms(datetime(2026, 9, 28, 11, tzinfo=UTC))
    assert forecast[-1][0] == ms(END - QUARTER)
    assert {value for _, value in consumption} == {40_000.0 + hour for hour in range(24)}
    assert {value for _, value in series(content, "solar")} == {100.0 * h for h in range(24)}
    assert {value for _, value in series(content, "co2")} == {20.0 + hour for hour in range(24)}
    assert [(item["id"], item["source"], item["unit"]) for item in content["series"]] == [
        ("price", "smard", "EUR/MWh"),
        ("consumption", "eco2mix", "MW"),
        ("rte_forecast", "eco2mix", "MW"),
        ("co2", "eco2mix", "gCO2/kWh"),
        ("solar", "eco2mix", "MW"),
        ("temperature", "openmeteo", "degC"),
        ("temperature_forecast", "openmeteo", "degC"),
    ]


def test_the_temperature_is_observed_then_forecast_by_the_latest_run(root: Path) -> None:
    content = export(root)
    observed, forecast = series(content, "temperature"), series(content, "temperature_forecast")
    last_observed = datetime(2026, 9, 28, 9, tzinfo=UTC)
    assert (observed[0][0], observed[-1][0]) == (ms(START), ms(last_observed))
    assert {value for _, value in observed} == {15.0}
    # The latest run, after the last observed hour, to the end of the period.
    assert (forecast[0][0], forecast[-1][0]) == (ms(last_observed + HOUR), ms(END - HOUR))
    assert {value for _, value in forecast} == {18.0}


def test_each_day_of_the_period_has_its_holidays_with_their_names_cleaned(root: Path) -> None:
    content = export(root)
    assert [day["day"] for day in content["days"]] == [
        (date(2026, 9, 21) + timedelta(days=n)).isoformat() for n in range(9)
    ]
    named = {day["day"]: day for day in content["days"]}
    # A control character or a change of direction is taken out.
    assert named["2026-09-23"]["public_holiday"] == "Jour de test"
    long = named["2026-09-25"]["school_holidays"]
    assert len(long) == LABEL and long.endswith("…")
    assert named["2026-09-24"] == {
        "day": "2026-09-24",
        "public_holiday": None,
        "school_holidays": None,
    }


def test_each_source_is_cited_with_its_licence_and_dates(root: Path) -> None:
    sources = {source["id"]: source for source in export(root)["sources"]}
    assert list(sources) == ["smard", "eco2mix", "openmeteo", "school-holidays", "public-holidays"]
    assert sources["eco2mix"] == {
        "id": "eco2mix",
        "name": "RTE, éCO2mix",
        "licence": "Licence Ouverte 2.0",
        # The most recent of its datasets.
        "updated_at": ms(datetime(2026, 9, 28, 11, 45, tzinfo=UTC)),
        "received_at": ms(RECEIVED),
    }
    assert sources["public-holidays"]["updated_at"] == ms(
        datetime(2026, 1, 2, 14, 32, 45, tzinfo=UTC)
    )
    assert sources["school-holidays"]["received_at"] == ms(datetime(2026, 9, 27, 20, tzinfo=UTC))
    # The CC BY sources are cited without a date.
    assert sources["smard"]["updated_at"] is None
    assert sources["openmeteo"]["name"] == "Weather data by Open-Meteo.com"
    # The latest reception of the values shown: here, that of the observations, after the run.
    assert sources["openmeteo"]["received_at"] == ms(RECEIVED)


def test_a_missing_table_leaves_its_series_empty_with_a_warning(root: Path) -> None:
    (root / "clean" / "openmeteo" / "weather.parquet").unlink()
    (root / "clean" / "eco2mix" / "updates.parquet").unlink()
    report = write_export(root, NOW)
    assert not report.failed
    assert any("weather.parquet" in warning for warning in report.warnings), report.warnings
    content = json.loads((root / EXPORT).read_bytes())
    assert series(content, "temperature") == series(content, "temperature_forecast") == []
    sources = {source["id"]: source for source in content["sources"]}
    assert sources["openmeteo"]["received_at"] is None
    assert sources["eco2mix"]["updated_at"] is None
    assert len(series(content, "price")) == 9 * 96


def test_a_value_that_is_not_finite_fails_the_export_and_keeps_the_one_before(
    root: Path,
) -> None:
    export(root)
    before = (root / EXPORT).read_bytes()
    prices = pl.read_parquet(root / "clean" / "smard" / "prices.parquet")
    write_parquet(
        prices.with_columns(pl.lit(float("nan")).alias("price_eur_per_mwh")),
        root / "clean" / "smard" / "prices.parquet",
    )
    report = write_export(root, NOW + HOUR)
    assert report.failed and "price" in report.errors[0]
    assert (root / EXPORT).read_bytes() == before


def test_a_table_that_does_not_read_is_an_error_and_the_rest_is_exported(root: Path) -> None:
    (root / "clean" / "smard" / "prices.parquet").write_bytes(b"not parquet")
    report = write_export(root, NOW)
    assert report.failed and "prices.parquet" in report.errors[0]
    content = json.loads((root / EXPORT).read_bytes())
    assert series(content, "price") == []
    assert len(series(content, "co2")) > 0


def test_a_table_of_another_shape_is_an_error_and_the_rest_is_exported(root: Path) -> None:
    path = root / "clean" / "eco2mix" / "measures.parquet"
    write_parquet(pl.read_parquet(path).rename({"measure": "quantity"}), path)
    prices = root / "clean" / "smard" / "prices.parquet"
    frame = pl.read_parquet(prices)
    write_parquet(frame.with_columns(pl.col("price_eur_per_mwh").cast(pl.String)), prices)
    report = write_export(root, NOW)
    assert [error.split(":")[0] for error in report.errors] == [
        f"{prices} has another shape, in price_eur_per_mwh",
        f'{path} does not read (unable to find column "measure"; valid columns',
    ]
    content = json.loads((root / EXPORT).read_bytes())
    assert series(content, "price") == series(content, "co2") == []
    assert len(series(content, "temperature")) > 0
