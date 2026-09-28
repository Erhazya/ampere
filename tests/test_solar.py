"""The production of a roof of 1 kWp from the weather, with pvlib (ADR 032)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pvlib
import pytest

from ampere.cli import main
from ampere.solar import (
    CALIBRATION,
    LATITUDE,
    LONGITUDE,
    ROOFS,
    Roof,
    calibration,
    calibration_report,
    load_factors,
    middle_of_hours,
    production,
    quarter_hours,
    regional_report,
    weather_columns,
)

SOUTH, SOUTH_EAST, SOUTH_WEST = (
    next(roof for roof in ROOFS if roof.name == name)
    for name in ("south", "south-east", "south-west")
)


def clear_sky(ends: list[datetime], temperature: float = 20.0) -> pl.DataFrame:
    """The weather of a clear sky at Lyon, as Open-Meteo dates it: each hour at its end."""
    import pandas as pd

    middles = pd.DatetimeIndex([end - timedelta(minutes=30) for end in ends])
    location = pvlib.location.Location(LATITUDE, LONGITUDE, altitude=164)
    sky = location.get_clearsky(middles)
    return pl.DataFrame(
        {
            "time": ends,
            "global_w_m2": sky["ghi"].to_numpy(),
            "direct_normal_w_m2": sky["dni"].to_numpy(),
            "diffuse_w_m2": sky["dhi"].to_numpy(),
            "temperature_c": [temperature] * len(ends),
            "wind_speed_m_s": [2.0] * len(ends),
        },
        schema_overrides={"time": pl.Datetime("us", "UTC")},
    )


def power(weather: pl.DataFrame, roof: Roof) -> list[float]:
    return production(weather, roof, calibration=1.0)["power_w_per_kwp"].to_list()


def test_the_roofs_of_the_neighbourhood() -> None:
    # pvlib counts an azimuth from the north: 180 degrees is the south (ADR 032).
    assert [(roof.name, roof.azimuth, roof.tilt) for roof in ROOFS] == [
        ("south-east", 135.0, 30.0),
        ("south", 180.0, 30.0),
        ("south-west", 225.0, 30.0),
    ]


def test_the_sun_is_taken_at_the_middle_of_each_hour() -> None:
    ends = [datetime(2024, 6, 21, 12, tzinfo=UTC)]
    assert middle_of_hours(pl.Series(ends, dtype=pl.Datetime("us", "UTC")))[0] == datetime(
        2024, 6, 21, 11, 30, tzinfo=UTC
    )


def test_a_roof_gives_nothing_at_night() -> None:
    assert power(clear_sky([datetime(2024, 6, 21, 23, tzinfo=UTC)]), SOUTH) == [0.0]


def test_a_clear_noon_of_june_gives_most_of_the_peak_power() -> None:
    # 11:00 to 12:00 UTC is 13:00 to 14:00 in Paris, around the solar noon of Lyon.
    (noon,) = power(clear_sky([datetime(2024, 6, 21, 12, tzinfo=UTC)]), SOUTH)
    assert 650 < noon < 950


def test_a_roof_facing_east_produces_in_the_morning_and_west_in_the_afternoon() -> None:
    weather = clear_sky(
        [datetime(2024, 6, 21, 8, tzinfo=UTC), datetime(2024, 6, 21, 16, tzinfo=UTC)]
    )
    east, west = power(weather, SOUTH_EAST), power(weather, SOUTH_WEST)
    assert east[0] > west[0]
    assert west[1] > east[1]


def test_heat_lowers_the_power() -> None:
    hour = [datetime(2024, 6, 21, 12, tzinfo=UTC)]
    assert power(clear_sky(hour, 35.0), SOUTH)[0] < power(clear_sky(hour, 5.0), SOUTH)[0]


def test_the_calibration_multiplies_the_power() -> None:
    weather = clear_sky([datetime(2024, 6, 21, 12, tzinfo=UTC)])
    calibrated = production(weather, SOUTH, calibration=1.1)["power_w_per_kwp"][0]
    assert calibrated == pytest.approx(1.1 * power(weather, SOUTH)[0])


def test_each_hour_gives_four_quarter_hours_of_the_same_power() -> None:
    hourly = pl.DataFrame(
        {"time": [datetime(2024, 6, 21, 13, tzinfo=UTC)], "power_w_per_kwp": [800.0]},
        schema_overrides={"time": pl.Datetime("us", "UTC")},
    )
    quarters = quarter_hours(hourly)
    # The hour that ends at 13:00 gives the quarter-hours that start from 12:00 to 12:45.
    assert quarters["start"].to_list() == [
        datetime(2024, 6, 21, 12, minute, tzinfo=UTC) for minute in (0, 15, 30, 45)
    ]
    assert quarters["power_w_per_kwp"].to_list() == [800.0] * 4


def test_the_observed_weather_of_the_clean_layer_becomes_columns() -> None:
    end = datetime(2024, 6, 21, 12, tzinfo=UTC)
    names = ["temperature_c", "global_w_m2", "direct_normal_w_m2", "diffuse_w_m2", "wind_speed_m_s"]
    long = pl.DataFrame(
        {
            "time": [end] * (len(names) + 1),
            "variable": [*names, "temperature_c"],
            "value": [21.0, 800.0, 700.0, 100.0, 2.0, 99.0],
            "kind": ["observed"] * len(names) + ["forecast"],
        },
        schema_overrides={"time": pl.Datetime("us", "UTC")},
    )
    wide = weather_columns(long)
    assert wide.columns == ["time", *sorted(names)]
    assert wide.row(0, named=True)["temperature_c"] == 21.0


def test_the_calibration_is_the_ratio_of_the_energies_of_pvgis_and_of_the_model() -> None:
    def hours(values: list[float], minute: int) -> pl.DataFrame:
        start = datetime(2023, 7, 1, 12, minute, tzinfo=UTC)
        return pl.DataFrame(
            {
                "time": [start + timedelta(days=31 * n) for n in range(len(values))],
                "orientation": ["south"] * len(values),
                "power_w_per_kwp": values,
            },
            schema_overrides={"time": pl.Datetime("us", "UTC")},
        )

    # July and August: PVGIS gives 10 % more in July and as much in August.
    ours, pvgis = hours([500.0, 400.0], 0), hours([550.0, 400.0], 10)
    months, factor = calibration(ours, pvgis)
    assert factor == pytest.approx(950 / 900)
    assert months["ratio"].to_list() == pytest.approx([1.1, 1.0])


def test_the_load_factors_of_each_month_and_their_correlation() -> None:
    hours = [datetime(2024, 1, 31, 22, tzinfo=UTC), datetime(2024, 1, 31, 23, tzinfo=UTC)]
    ours = pl.DataFrame({"hour": hours, "ours": [10.0, 30.0]})
    region = pl.DataFrame({"hour": hours, "region": [20.0, 40.0]})
    months, correlation = load_factors(ours, region)
    # 22:00 UTC on 31 January is already February in Paris.
    assert [month.month for month in months["month"]] == [1, 2]
    assert months["ratio"].to_list() == pytest.approx([0.5, 0.75])
    assert correlation == pytest.approx(1.0)


def write_clean_layer(clean: Path) -> None:
    """A clean layer of three clear hours of July 2023: the weather, PVGIS and the region."""
    ends = [datetime(2023, 7, 3, hour, tzinfo=UTC) for hour in (10, 11, 12)]
    wide = clear_sky(ends)
    weather = wide.unpivot(index="time", variable_name="variable", value_name="value").with_columns(
        pl.lit("observed").alias("kind")
    )
    (clean / "openmeteo").mkdir(parents=True)
    weather.write_parquet(clean / "openmeteo" / "weather.parquet")
    pvgis = pl.DataFrame(
        {
            "time": [end - timedelta(minutes=50) for end in ends for _ in ROOFS],
            "orientation": [roof.name for _ in ends for roof in ROOFS],
            "power_w_per_kwp": [700.0] * 9,
        },
        schema_overrides={"time": pl.Datetime("us", "UTC")},
    )
    (clean / "pvgis").mkdir()
    pvgis.write_parquet(clean / "pvgis" / "production.parquet")
    region = pl.DataFrame(
        {
            "start": [end - timedelta(minutes=minutes) for end in ends for minutes in (60, 30)],
            "area": ["ARA"] * 6,
            "measure": ["solar_load_factor_pct"] * 6,
            "value": [60.0, 60.0, 70.0, 70.0, 65.0, 65.0],
        },
        schema_overrides={"start": pl.Datetime("us", "UTC")},
    )
    (clean / "eco2mix").mkdir()
    region.write_parquet(clean / "eco2mix" / "measures.parquet")


def test_the_reports_read_the_clean_layer_through_the_guard(tmp_path: Path) -> None:
    write_clean_layer(tmp_path)
    months, factor = calibration_report(tmp_path)
    assert months.height == 3 and 0.7 < factor < 1.3
    start = datetime(2023, 7, 3, 9, tzinfo=UTC)
    regional, correlation = regional_report(tmp_path, start, start + timedelta(hours=3))
    assert regional.height == 1 and -1 <= correlation <= 1


def test_the_check_fails_without_the_clean_layer() -> None:
    # The tests point AMPERE_DATA to an empty folder (conftest.py).
    assert main(["solar", "check"]) == 1


def test_the_factor_in_the_code_is_near_1() -> None:
    assert abs(CALIBRATION - 1) <= 0.10
