"""PVGIS: the hourly production of 2023 of a roof of 1 kWp at Lyon, for each orientation of the
roofs of the neighbourhood, to calibrate the solar model (ADR 032).

An archive that no longer changes: `ampere ingest pvgis` asks for it, never the daily job. Each
answer is kept in the raw layer when it changes; clean/pvgis/production.parquet is rebuilt from
the last readable answer of each orientation.
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx2
import polars as pl

from ampere.data.clean import Report, write_parquet
from ampere.data.raw import DamagedRawFile, RawStore
from ampere.sources.archive import JSON, PAUSE, SINCE, fetch
from ampere.sources.shapes import SchemaError, finite, is_int, load_json

log = logging.getLogger(__name__)

SOURCE = "pvgis"
DATASET = "hourly"
URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
# The roof of ADR 032 at the point of the neighbourhood (ADR 002): 1 kWp, 30 degrees, and the
# 14 % of losses that PVGIS takes by default.
LATITUDE, LONGITUDE = 45.76, 4.84
YEAR = 2023
TILT = 30
LOSS = 14
# PVGIS counts an azimuth from the south, positive to the west.
ORIENTATIONS = {"south-east": -45, "south": 0, "south-west": 45}
# A year of 2023 has 8,760 hours; an answer weighs about 1 MB.
HOURS = 8_760
MAX_BYTES = 3_000_000
# A roof of 1 kWp gives at most about 1 kW, a little more on a cold and bright day.
MAX_POWER_W = 1_100.0
STAMP = re.compile(r"[0-9]{8}:[0-9]{4}")
SCHEMA = pl.Schema(
    {
        "time": pl.Datetime("us", "UTC"),
        "orientation": pl.String(),
        "power_w_per_kwp": pl.Float64(),
        "received_at": pl.Datetime("us", "UTC"),
    }
)


def url(aspect: int) -> str:
    """The question to PVGIS for one orientation."""
    query = {
        "lat": LATITUDE,
        "lon": LONGITUDE,
        "startyear": YEAR,
        "endyear": YEAR,
        "pvcalculation": 1,
        "peakpower": 1,
        "loss": LOSS,
        "angle": TILT,
        "aspect": aspect,
        "outputformat": "json",
    }
    return f"{URL}?{urlencode(query)}"


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
    """Ask PVGIS for each orientation, then rebuild the clean table from the last readable answer
    of each. The archive does not change: `full` changes nothing, nor does `since`."""
    kept = len(store.receipts(SOURCE))
    report = Report()
    for number, (name, aspect) in enumerate(ORIENTATIONS.items()):
        if number:
            sleep(PAUSE)
        fetch(
            http,
            store,
            source=SOURCE,
            dataset=DATASET,
            url=url(aspect),
            content_type=JSON,
            extension="json",
            max_bytes=MAX_BYTES,
            sleep=sleep,
            request=name,
        )
    frames = [last_readable(store, name, report) for name in ORIENTATIONS]
    if all(frame is not None for frame in frames):
        frame = pl.concat([frame for frame in frames if frame is not None]).sort(
            "orientation", "time"
        )
        report.warnings.extend(check(frame))
        write_parquet(frame, clean / SOURCE / "production.parquet")
    report.errors.extend(f"raw layer: {problem}" for problem in store.verify(SOURCE))
    log.info(
        "%s: %d requests, %d new responses",
        SOURCE,
        len(ORIENTATIONS),
        len(store.receipts(SOURCE)) - kept,
    )
    return report


def last_readable(store: RawStore, name: str, report: Report) -> pl.DataFrame | None:
    """The hours of the last readable answer for an orientation. The last answer that does not
    read is an error; an older one is only logged."""
    receipts = [
        receipt
        for receipt in store.receipts(SOURCE)
        if receipt.dataset == DATASET and receipt.request == name
    ]
    for receipt in reversed(receipts):
        try:
            hours = parse(store.read(receipt), receipt.url, name)
            return hours.with_columns(pl.lit(receipt.received_at).alias("received_at")).cast(SCHEMA)
        except (DamagedRawFile, SchemaError) as error:
            if receipt == receipts[-1]:
                report.errors.append(f"{name}: {error}")
            else:
                log.warning(
                    "%s: the answer received at %s does not read (%s)",
                    name,
                    receipt.received_at,
                    error,
                )
    report.errors.append(f"{name}: no readable answer")
    return None


def parse(content: bytes, url: str, name: str) -> pl.DataFrame:
    """The hours of an answer of PVGIS, once checked: the roof asked for, the year 2023, and every
    hour of it once, in order, with a power between 0 and MAX_POWER_W."""
    body = load_json(content, url)
    inputs = dig(body, url, "inputs")
    fixed = dig(inputs, url, "mounting_system", "fixed")
    # Numbers, not booleans: Python takes JSON's true for 1 and its false for 0.
    roof = (
        finite(dig(inputs, url, "location", "latitude")),
        finite(dig(inputs, url, "location", "longitude")),
        finite(dig(fixed, url, "slope", "value")),
        finite(dig(fixed, url, "azimuth", "value")),
        finite(dig(inputs, url, "pv_module", "peak_power")),
        finite(dig(inputs, url, "pv_module", "system_loss")),
    )
    if roof != (LATITUDE, LONGITUDE, TILT, ORIENTATIONS[name], 1, LOSS):
        raise SchemaError(f"{url}: an answer for another roof than {name}: {roof}")
    years = (dig(inputs, url, "meteo_data", "year_min"), dig(inputs, url, "meteo_data", "year_max"))
    if not all(is_int(year) for year in years) or years != (YEAR, YEAR):
        raise SchemaError(f"{url}: an answer for {years}, not for {YEAR}")
    rows = dig(body, url, "outputs", "hourly")
    if not isinstance(rows, list) or len(rows) != HOURS:
        raise SchemaError(f"{url}: not the {HOURS} hours of {YEAR}")
    times, powers = [], []
    expected = datetime(YEAR, 1, 1, tzinfo=UTC)
    for row in rows:
        stamp = row.get("time") if isinstance(row, dict) else None
        moment = pvgis_time(stamp)
        if moment is None:
            raise SchemaError(f"{url}: the hours are not dated as PVGIS dates them: {stamp!r}")
        if moment.replace(minute=0) != expected:
            raise SchemaError(f"{url}: the hours do not follow each other at {stamp}")
        value = row.get("P")
        power = finite(value)
        if power is None or not 0 <= power <= MAX_POWER_W:
            raise SchemaError(f"{url}: a power out of 0 to {MAX_POWER_W} W at {stamp}: {value!r}")
        times.append(moment)
        powers.append(power)
        expected += timedelta(hours=1)
    return pl.DataFrame(
        {"time": times, "orientation": [name] * HOURS, "power_w_per_kwp": powers},
        schema={
            "time": pl.Datetime("us", "UTC"),
            "orientation": pl.String(),
            "power_w_per_kwp": pl.Float64(),
        },
    )


def pvgis_time(stamp: object) -> datetime | None:
    """The instant of a stamp of PVGIS, such as 20230101:0010, in UTC; None if it is not one.
    PVGIS dates each hour of its satellite a few minutes past the hour."""
    if not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
        return None
    try:
        return datetime.strptime(stamp, "%Y%m%d:%H%M").replace(tzinfo=UTC)
    except ValueError:  # the shape of a stamp, but no date, such as 20230229:0010
        return None


def dig(value: Any, url: str, *keys: str) -> Any:
    """A field of an answer, found key by key; a SchemaError when one is missing."""
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise SchemaError(f"{url}: no field {'.'.join(keys)}")
        value = value[key]
    return value


def check(frame: pl.DataFrame) -> list[str]:
    """Warnings about a production that PVGIS gives but that would be a surprise: the south
    produces the most over a year."""
    energy = dict(frame.group_by("orientation").agg(pl.col("power_w_per_kwp").sum() / 1000).rows())
    if energy["south"] < max(energy["south-east"], energy["south-west"]):
        return [f"pvgis: the south does not produce the most in {YEAR}: {energy} kWh per kWp"]
    return []
