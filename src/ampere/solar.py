"""The production of a roof of 1 kWp, hour by hour, from the weather of Open-Meteo, computed with
pvlib as close to PVGIS as pvlib allows, then calibrated on PVGIS (ADR 032).

PVGIS computes the losses by reflection with the model of Martin and Ruiz, the temperature of the
cells with Faiman's, and the power of crystalline silicon modules with Huld's: pvlib has all
three. It lacks Muneer's transposition, which PVGIS uses for the diffuse light on a tilted plane:
Perez's, the most cited, takes its place. The factor CALIBRATION corrects what is left, mostly
the difference between the irradiance of Open-Meteo and that of PVGIS's satellite.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pvlib

from ampere.data.periods import scan

# The point of the neighbourhood (ADR 002), and its height as PVGIS gives it.
LATITUDE, LONGITUDE, ELEVATION = 45.76, 4.84, 164.0
TILT = 30.0
# The losses of the system that PVGIS takes by default: cables, inverter, dirt, ageing.
LOSSES = 0.14
# PVGIS's coefficients: Faiman's for a free-standing module, Martin and Ruiz's for glass.
FAIMAN_U0, FAIMAN_U1 = 26.9, 6.2
MARTIN_RUIZ_A_R = 0.16
# The ratio of the energy of PVGIS to that of the model, over July to December 2023, for the three
# orientations, measured on 28 September 2026: `ampere solar check` computes it again, and fails
# when the data no longer give it (docs/mesures.md).
CALIBRATION = 0.954
# The hours that PVGIS and the observed weather of Open-Meteo share: July to December 2023, before
# 15 March 2024, the period of the calibration of the neighbourhood (ADR 007 and 032).
CALIBRATION_START = datetime(2023, 7, 1, tzinfo=UTC)
CALIBRATION_END = datetime(2024, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
# The columns of the weather that the model reads, as the clean layer names them (ADR 025).
WEATHER = ("diffuse_w_m2", "direct_normal_w_m2", "global_w_m2", "temperature_c", "wind_speed_m_s")


@dataclass(frozen=True)
class Roof:
    """A roof: its name, its azimuth in degrees from the north, clockwise, and its tilt."""

    name: str
    azimuth: float
    tilt: float = TILT


# The three orientations of the equipped roofs (ADR 032).
ROOFS = (Roof("south-east", 135.0), Roof("south", 180.0), Roof("south-west", 225.0))


def weather_columns(weather: pl.DataFrame) -> pl.DataFrame:
    """The observed weather of the clean layer, one column per variable the model reads."""
    return (
        weather.filter(
            pl.col("kind") == "observed", pl.col("variable").cast(pl.String).is_in(WEATHER)
        )
        .with_columns(pl.col("variable").cast(pl.String))
        .pivot(on="variable", index="time", values="value")
        .select("time", *WEATHER)
        .sort("time")
    )


def middle_of_hours(ends: pl.Series) -> pl.Series:
    """The middle of each hour that Open-Meteo dates with its end."""
    return ends - timedelta(minutes=30)


def production(
    weather: pl.DataFrame, roof: Roof, *, calibration: float = CALIBRATION
) -> pl.DataFrame:
    """The power of a roof of 1 kWp, in W, for each hour of the weather, dated like it with the
    end of the hour. A ValueError when a value of the weather is missing: an hour without
    irradiance is not an hour without sun, and must not count as 0 W."""
    holes = weather.filter(
        pl.any_horizontal(pl.col(name).is_null() | pl.col(name).is_nan() for name in WEATHER)
    )["time"]
    if holes.len():
        raise ValueError(
            f"the weather lacks values for {holes.len()} hour(s), the first ending at "
            f"{holes.dt.min():%Y-%m-%d %H:%M} UTC"
        )
    middles = pd.DatetimeIndex(
        middle_of_hours(weather["time"]).dt.replace_time_zone(None).to_numpy(), tz="UTC"
    )

    def column(name: str) -> pd.Series:
        return pd.Series(weather[name].to_numpy(), index=middles)

    sun = pvlib.solarposition.get_solarposition(middles, LATITUDE, LONGITUDE, altitude=ELEVATION)
    zenith, azimuth = sun["apparent_zenith"], sun["azimuth"]
    plane = pvlib.irradiance.get_total_irradiance(
        roof.tilt,
        roof.azimuth,
        zenith,
        azimuth,
        dni=column("direct_normal_w_m2"),
        ghi=column("global_w_m2"),
        dhi=column("diffuse_w_m2"),
        dni_extra=pvlib.irradiance.get_extra_radiation(middles),
        airmass=pvlib.atmosphere.get_relative_airmass(zenith),
        model="perez",
    )
    incidence = pvlib.irradiance.aoi(roof.tilt, roof.azimuth, zenith, azimuth)
    diffuse = pvlib.iam.martin_ruiz_diffuse(roof.tilt, a_r=MARTIN_RUIZ_A_R)
    effective = (
        plane["poa_direct"] * pvlib.iam.martin_ruiz(incidence, a_r=MARTIN_RUIZ_A_R)
        + plane["poa_sky_diffuse"] * diffuse["sky"]
        + plane["poa_ground_diffuse"] * diffuse["ground"]
    )
    cells = pvlib.temperature.faiman(
        plane["poa_global"], column("temperature_c"), column("wind_speed_m_s"), FAIMAN_U0, FAIMAN_U1
    )
    power = pvlib.pvarray.huld(effective, cells, pdc0=1000.0, cell_type="csi", k_version="pvgis5")
    watts = np.clip(np.asarray(power, dtype=float), 0.0, None)
    return pl.DataFrame(
        {"time": weather["time"], "power_w_per_kwp": watts * (1 - LOSSES) * calibration}
    )


def quarter_hours(hourly: pl.DataFrame) -> pl.DataFrame:
    """The four quarter-hours of each hour, dated by their start, with the power of their hour:
    the energy of the hour is kept, as ADR 005 does for the half-hours of Enedis."""
    before = pl.DataFrame({"minutes": [60, 45, 30, 15]})
    return (
        hourly.join(before, how="cross")
        .select(
            (pl.col("time") - pl.duration(minutes=pl.col("minutes"))).alias("start"),
            "power_w_per_kwp",
        )
        .sort("start")
    )


def calibration(ours: pl.DataFrame, pvgis: pl.DataFrame) -> tuple[pl.DataFrame, float]:
    """The energy of each orientation and month, for the model and for PVGIS, their ratio, and the
    factor of the whole period, over the hours both have: an hour that one of them lacks would
    lower its side only. The model dates an hour with its end, PVGIS a few minutes after its
    start: each hour counts in the month where it starts."""

    def hourly(frame: pl.DataFrame, start: pl.Expr, name: str) -> pl.DataFrame:
        return frame.select(
            "orientation", start.alias("hour"), pl.col("power_w_per_kwp").alias(name)
        )

    months = (
        hourly(ours, pl.col("time") - HOUR, "model")
        .join(hourly(pvgis, pl.col("time").dt.truncate("1h"), "pvgis"), on=["orientation", "hour"])
        .group_by("orientation", pl.col("hour").dt.truncate("1mo").alias("month"))
        .agg(
            pl.len().alias("hours"),
            (pl.col("model").sum() / 1000).alias("model_kwh"),
            (pl.col("pvgis").sum() / 1000).alias("pvgis_kwh"),
        )
        .with_columns((pl.col("pvgis_kwh") / pl.col("model_kwh")).alias("ratio"))
        .sort("orientation", "month")
    )
    return months, float(months["pvgis_kwh"].sum()) / float(months["model_kwh"].sum())


def roofs_production(weather: pl.DataFrame, *, calibration: float) -> pl.DataFrame:
    """The production of each roof of 1 kWp, hour by hour, with its orientation."""
    return pl.concat(
        [
            production(weather, roof, calibration=calibration).with_columns(
                pl.lit(roof.name).alias("orientation")
            )
            for roof in ROOFS
        ]
    )


def calibration_report(clean: Path) -> tuple[pl.DataFrame, float]:
    """The model without calibration against PVGIS, month by month, over the hours they share:
    the weather that ends them from 01:00 on 1 July 2023 to 00:00 on 1 January 2024, UTC."""
    weather = scan(
        clean / "openmeteo" / "weather.parquet",
        "time",
        CALIBRATION_START + HOUR,
        CALIBRATION_END + HOUR,
    ).collect()
    pvgis = scan(
        clean / "pvgis" / "production.parquet", "time", CALIBRATION_START, CALIBRATION_END
    ).collect()
    return calibration(roofs_production(weather_columns(weather), calibration=1.0), pvgis)


def regional_report(clean: Path, start: datetime, end: datetime) -> tuple[pl.DataFrame, float]:
    """The load factor of the three roofs, calibrated, against that of the solar of the region
    that éCO2mix publishes, month by month, and their correlation hour by hour, over the hours
    from start to end."""
    weather = scan(clean / "openmeteo" / "weather.parquet", "time", start + HOUR, end).collect()
    ours = (
        roofs_production(weather_columns(weather), calibration=CALIBRATION)
        .group_by("time")
        .agg(pl.col("power_w_per_kwp").mean())
        # 1 kWp at full power is a load factor of 100 %.
        .select(
            (pl.col("time") - HOUR).alias("hour"), (pl.col("power_w_per_kwp") / 10).alias("ours")
        )
    )
    region = (
        scan(clean / "eco2mix" / "measures.parquet", "start", start, end)
        .filter(pl.col("area") == "ARA", pl.col("measure") == "solar_load_factor_pct")
        .group_by(pl.col("start").dt.truncate("1h").alias("hour"))
        .agg(pl.col("value").mean().alias("region"))
        .collect()
    )
    return load_factors(ours, region)


def load_factors(ours: pl.DataFrame, region: pl.DataFrame) -> tuple[pl.DataFrame, float]:
    """The mean load factors of each Paris month, in %, their ratio, and the correlation of the
    two, hour by hour, over the hours both have."""
    both = ours.join(region, on="hour")
    months = (
        both.group_by(
            pl.col("hour").dt.convert_time_zone("Europe/Paris").dt.truncate("1mo").alias("month")
        )
        .agg(pl.col("ours").mean(), pl.col("region").mean())
        .with_columns((pl.col("ours") / pl.col("region")).alias("ratio"))
        .sort("month")
    )
    correlation = both.select(pl.corr("ours", "region")).item()
    return months, float(correlation)
