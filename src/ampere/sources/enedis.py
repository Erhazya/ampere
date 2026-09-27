"""Enedis: the half-hourly consumption of the homes of Auvergne-Rhône-Alpes, and the injection of
rooftop solar, segment by segment (ADR 026).

Enedis publishes a three-year window every quarter. Each run reads the date of the last
publication of both datasets in the metadata of Enedis's native API. For 7 days after a new
publication, it asks for the Paris months from July 2023 to the current one, one Parquet export per
month, through the compatibility layer of the old API; the rest of the time, only the months whose
last response does not read, or is empty inside the window, the three years that end with the last
month published. It keeps every response in the raw layer, rebuilds
clean/enedis/consumption.parquet and clean/enedis/solar.parquet from the last readable response
with rows of each month, and checks them.
"""

import io
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx2
import polars as pl

from ampere.data.clean import Report, write_parquet
from ampere.data.days import PARIS, day_bounds, every_day, paris_day, paris_months, quarter_hours
from ampere.data.raw import DamagedRawFile, RawStore, Receipt
from ampere.sources.archive import JSON, fetch
from ampere.sources.shapes import SchemaError, load_json

log = logging.getLogger(__name__)

SOURCE = "enedis"
NATIVE = "https://opendata.enedis.fr/data-fair/api/v1/datasets"
EXPORTS = "https://opendata.enedis.fr/api/explore/v2.1/catalog/datasets"
PARQUET = "application/vnd.apache.parquet"
PUBLICATION = "publication"
# The start of the history, shared by every source.
SINCE = date(2023, 7, 1)
# After a new publication, every month is asked again for this many Paris days, that of its first
# reception included: a run cut short leaves no month at the publication before.
REFRESH = timedelta(days=7)
# Seconds between two requests, out of politeness.
PAUSE = 0.5
MAX_METADATA_BYTES = 1_000_000
# The largest month accepted: a month of residential consumption weighs 805 kB.
MAX_MONTH_BYTES = 20_000_000
# Parquet compresses by itself: a few kilobytes can hold millions of rows, which Polars would
# unfold into gigabytes. The footer tells how many before any is read: a month of consumption has
# 58,000 rows in 15 columns.
MAX_ROWS = 500_000
MAX_COLUMNS = 30
# Enedis publishes a quarter about a month after its end: past this, a publication is late.
STALE_AFTER = timedelta(days=150)
HALF_HOUR = timedelta(minutes=30)
# A publication covers up to about a month before its date: a clean table that ends much earlier
# lacks some of it.
LAGGING = timedelta(days=60)
# Over three years, the statistical secrecy masks at most 7.7 % of the global curve of a month
# for homes, and 1.2 % for rooftop solar: far more is a publication that masks it by mistake. For
# homes, 20 % is 4 to 6 of the 39 segments masked for a whole month.
MASKED_AT_MOST = 0.2
# Invalid rows listed in a report: the others are only counted.
INVALID_LISTED = 100
# The step of a value that stands for a whole day: a count of sites, or a curve that the
# statistical secrecy only publishes as the mean of its day.
DAY_MINUTES = 1440
MEASURES = ("sites", "total_w", "mean_w", "mean_1_w", "mean_2_w")
# The mean curves, all of the sites with a smart meter and each half of them, in Wh per half-hour.
CURVES = {
    "mean_w": "courbe_moyenne_ndeg1_ndeg2_wh",
    "mean_1_w": "courbe_moyenne_ndeg1_wh",
    "mean_2_w": "courbe_moyenne_ndeg2_wh",
}
# The share of the sites of a segment in each curve, in %: a whole number, "< 1" for a sliver of
# a large segment, or "S" when the statistical secrecy masks the curve.
INDICES = {
    "courbe_moyenne_ndeg1_ndeg2_wh": "indice_representativite_courbe_ndeg1_ndeg2",
    "courbe_moyenne_ndeg1_wh": "indice_representativite_courbe_ndeg1",
    "courbe_moyenne_ndeg2_wh": "indice_representativite_courbe_ndeg2",
}
# The instants of an export: UTC, although Parquet gives them without a time zone.
STARTS = pl.col("horodate").dt.cast_time_unit("us").dt.replace_time_zone("UTC")


@dataclass(frozen=True)
class Dataset:
    """A dataset of Enedis as Ampère takes it: its name there and in the raw and clean layers, the
    filter of its rows and the values it allows, its segment columns with their clean names, its
    count and total columns, the most a mean curve can be, in W, the segments it must have, each
    as its values in the order of its columns, and its window: the number of months Enedis
    publishes, which end with the last month published."""

    name: str
    kind: str
    where: str
    allowed: tuple[tuple[str, tuple[str, ...]], ...]
    prefixed: tuple[tuple[str, str], ...]
    segment: tuple[tuple[str, str], ...]
    sites: str
    total: str
    highest_mean: float
    expected: tuple[tuple[str, ...], ...]
    window: int

    @property
    def keys(self) -> list[str]:
        """The clean columns that name a segment."""
        return [clean for _, clean in self.segment]

    @property
    def schema(self) -> pl.Schema:
        return pl.Schema(
            {
                "start": pl.Datetime("us", "UTC"),
                # Categories: a few labels, repeated over ten million rows.
                **dict.fromkeys(self.keys, pl.Categorical()),
                "measure": pl.Enum(MEASURES),
                "value": pl.Float64(),
                "step_minutes": pl.UInt16(),
                "received_at": pl.Datetime("us", "UTC"),
            }
        )


# The ranges of subscribed power of each profile of homes in the region, P0 standing for all of
# them: 39 segments, as Enedis published them on 27 September 2026.
HOME_PROFILES = {
    "RES1 (+ RES1WE)": ("P0: Total <= 36 kVA", "P1: ]0-3] kVA", "P2: ]3-6] kVA"),
    "RES11 (+ RES11WE)": (
        "P0: Total <= 36 kVA",
        "P3: ]6-9] kVA",
        "P4: ]9-12] kVA",
        "P5: ]12-15] kVA",
        "P6: ]15-18] kVA",
        "P7: ]18-24] kVA",
        "P8: ]24-30] kVA",
        "P9: ]30-36] kVA",
    ),
    "RES2 (+ RES5)": (
        "P0: Total <= 36 kVA",
        "P1: ]0-6] kVA",
        "P3: ]6-9] kVA",
        "P4: ]9-12] kVA",
        "P5: ]12-15] kVA",
        "P6: ]15-18] kVA",
        "P7: ]18-24] kVA",
        "P8: ]24-30] kVA",
        "P9: ]30-36] kVA",
    ),
    "RES2WE": (
        "P0: Total <= 36 kVA",
        "P1: ]0-6] kVA",
        "P3: ]6-9] kVA",
        "P4: ]9-12] kVA",
        "P5: ]12-15] kVA",
        "P6: ]15-36] kVA",
    ),
    "RES3": (
        "P0: Total <= 36 kVA",
        "P1: ]0-9] kVA",
        "P4: ]9-12] kVA",
        "P5: ]12-15] kVA",
        "P6: ]15-18] kVA",
        "P7: ]18-30] kVA",
        "P9: ]30-36] kVA",
    ),
    "RES4": (
        "P0: Total <= 36 kVA",
        "P1: ]0-9] kVA",
        "P4: ]9-12] kVA",
        "P5: ]12-15] kVA",
        "P6: ]15-18] kVA",
        "P7: ]18-36] kVA",
    ),
}
CONSUMPTION = Dataset(
    name="conso-inf36-region",
    kind="consumption",
    where="code_region='84' and startswith(profil, 'RES')",
    allowed=(("code_region", ("84",)),),
    prefixed=(("profil", "RES"),),
    segment=(("profil", "profile"), ("plage_de_puissance_souscrite", "power_range")),
    sites="nb_points_soutirage",
    total="total_energie_soutiree_wh",
    highest_mean=36_000,
    expected=tuple(
        (profile, power) for profile, ranges in HOME_PROFILES.items() for power in ranges
    ),
    window=36,
)
ROOFTOPS = ("P1 : ]0 - 3] kW", "P2 : ]3 - 9] kW")
SOLAR = Dataset(
    name="prod-region",
    kind="solar",
    where="code_region='84' and filiere_de_production='F5 : Solaire' and "
    f"plage_de_puissance_injection in ({', '.join(f"'{power}'" for power in ROOFTOPS)})",
    allowed=(
        ("code_region", ("84",)),
        ("filiere_de_production", ("F5 : Solaire",)),
        ("plage_de_puissance_injection", ROOFTOPS),
    ),
    prefixed=(),
    segment=(("plage_de_puissance_injection", "power_range"),),
    sites="nb_points_injection",
    total="total_energie_injectee_wh",
    highest_mean=9_000,
    expected=tuple((power,) for power in ROOFTOPS),
    window=36,
)
DATASETS = (CONSUMPTION, SOLAR)


@dataclass(frozen=True)
class Month:
    """A Paris month of a dataset: its name in the raw layer and its bounds in UTC."""

    dataset: Dataset
    start: datetime
    end: datetime

    @property
    def name(self) -> str:
        return f"{self.start.astimezone(PARIS):%Y-%m}"

    @property
    def url(self) -> str:
        """The Parquet export of the rows Ampère takes, over the month."""
        where = (
            f"{self.dataset.where} and horodate >= '{self.start.isoformat()}' "
            f"and horodate < '{self.end.isoformat()}'"
        )
        return f"{EXPORTS}/{self.dataset.name}/exports/parquet?{urlencode({'where': where})}"


def metadata_url(dataset: Dataset) -> str:
    return f"{NATIVE}/{dataset.name}"


def months(dataset: Dataset, since: date, today: date) -> list[Month]:
    """The Paris months of a dataset, from the one of `since` to the one of today."""
    return [Month(dataset, start, end) for start, end in paris_months(since, today)]


def ingest(
    http: httpx2.Client,
    store: RawStore,
    clean: Path,
    *,
    now: datetime,
    since: date = SINCE,
    full: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    datasets: Sequence[Dataset] = DATASETS,
) -> Report:
    """Read the date of the last publication of each dataset. For 7 days after a new one, or when
    full, fetch every month; otherwise only those whose last response does not read, or is empty
    inside the window. Then rebuild each clean file from the last readable response with rows of
    each month, check it, and replace it if its rows are valid."""
    kept = len(store.receipts(SOURCE))
    report = Report()
    publications: dict[str, datetime] = {}
    # The datasets whose every month this run asks for: --full could do no more.
    refreshed: set[str] = set()
    asked = 0

    def ask(dataset: str, request: str, url: str, extension: str) -> tuple[bytes, Receipt]:
        nonlocal asked
        if asked:
            sleep(PAUSE)
        asked += 1
        parquet = extension == "parquet"
        return fetch(
            http,
            store,
            source=SOURCE,
            dataset=dataset,
            url=url,
            content_type=PARQUET if parquet else JSON,
            extension=extension,
            max_bytes=MAX_MONTH_BYTES if parquet else MAX_METADATA_BYTES,
            sleep=sleep,
            request=request,
        )

    for dataset in datasets:
        content, receipt = ask(PUBLICATION, dataset.name, metadata_url(dataset), "json")
        refresh = full
        try:
            published = parse_publication(content, receipt.url)
            if published > now + timedelta(days=1):
                raise SchemaError(
                    f"{receipt.url}: a publication dated {published:%Y-%m-%d %H:%M} UTC, "
                    "after the run"
                )
            publications[dataset.name] = published
        except SchemaError as error:
            # Without the date of the publication, the months are asked for as on an ordinary
            # day.
            report.errors.append(str(error))
        else:
            # A publication seen to replace another opens the window at its first reception;
            # the first one ever seen, at its own date.
            opened = first_reception(store, dataset, published) or published
            # In Paris days: the start of a run and a reception come from two clocks, and the
            # eighth run would otherwise fall on either side by a fraction of a second.
            if paris_day(now) - paris_day(opened) < REFRESH:
                refresh = True
                log.info("%s: publication of %s, every month asked", dataset.name, published)
        wanted = months(dataset, since, paris_day(now))
        if refresh:
            refreshed.add(dataset.name)
        else:
            wanted = to_ask(store, responses(store), wanted)
        for month in wanted:
            content, receipt = ask(dataset.kind, month.name, month.url, "parquet")
            try:
                read_month(content, month, receipt.url)
            except SchemaError as error:
                # A faulty month is an error; the run goes on with what the raw layer has.
                report.errors.append(str(error))
    history = responses(store)
    for dataset in datasets:
        frame, built = build(store, history, dataset, since=since, today=paris_day(now))
        report.errors.extend(built.errors)
        report.warnings.extend(built.warnings)
        asked_all = dataset.name in refreshed
        report.warnings.extend(
            late_months(frame, dataset, publications.get(dataset.name), asked_all=asked_all)
        )
        found = check(frame, dataset, since=since, now=now, asked_all=asked_all)
        report.invalid.extend(found.invalid)
        report.errors.extend(found.errors)
        report.warnings.extend(found.warnings)
        path = clean / SOURCE / f"{dataset.kind}.parquet"
        if found.invalid:
            log.error("%s kept as it was: the new values break its rules", path)
        else:
            write_parquet(frame, path)
    report.errors.extend(f"raw layer: {problem}" for problem in store.verify(SOURCE))
    log.info(
        "%s: %d requests, %d new responses",
        SOURCE,
        asked,
        len(store.receipts(SOURCE)) - kept,
    )
    return report


def responses(store: RawStore) -> dict[tuple[str, str], list[Receipt]]:
    """The responses kept for each request of each dataset, in the order they came."""
    kept: dict[tuple[str, str], list[Receipt]] = {}
    for receipt in store.receipts(SOURCE):
        kept.setdefault((receipt.dataset, receipt.request), []).append(receipt)
    return kept


def first_reception(store: RawStore, dataset: Dataset, published: datetime) -> datetime | None:
    """When the last publication of a dataset came to replace another: the reception of the
    earliest of the last metadata responses that give its date. None when no earlier response
    gives another date: the publication is then the first one ever seen."""
    receipts = responses(store).get((PUBLICATION, dataset.name), [])
    first = None
    for receipt in reversed(receipts):
        try:
            if parse_publication(store.read(receipt), receipt.url) != published:
                return first
        except (DamagedRawFile, SchemaError):
            return first
        first = receipt.received_at
    return None


def rows_of(store: RawStore, receipt: Receipt, month: Month) -> pl.DataFrame | None:
    """The rows of a kept response of a month, checked; None when it does not read, logging
    why."""
    try:
        return read_month(store.read(receipt), month, receipt.url)
    except (DamagedRawFile, SchemaError) as error:
        log.warning(
            "%s %s: the response received at %s does not read (%s)",
            month.dataset.kind,
            month.name,
            receipt.received_at,
            error,
        )
        return None


def to_ask(
    store: RawStore, history: dict[tuple[str, str], list[Receipt]], wanted: list[Month]
) -> list[Month]:
    """The months to ask for again outside the days after a publication: those without a
    readable last response, and those whose last response is empty although they belong to the
    window of their dataset, the months that end with the last one published. The months that
    have left it, and those not published yet, stay empty."""
    counts: list[int | None] = []
    # The last month whose last response has rows, or does not read: Enedis may publish it.
    last: int | None = None
    for i, month in enumerate(wanted):
        receipts = history.get((month.dataset.kind, month.name), [])
        raw = rows_of(store, receipts[-1], month) if receipts else None
        counts.append(None if raw is None else raw.height)
        if receipts and (raw is None or raw.height):
            last = i
    return [
        month
        for i, (month, count) in enumerate(zip(wanted, counts, strict=True))
        if count is None
        or (count == 0 and last is not None and last - month.dataset.window < i < last)
    ]


def latest_with_rows(
    store: RawStore, receipts: list[Receipt], month: Month
) -> tuple[pl.DataFrame, Receipt] | None:
    """The rows of the latest of these responses of a month that reads and has rows."""
    for receipt in reversed(receipts):
        raw = rows_of(store, receipt, month)
        if raw is not None and raw.height:
            return raw, receipt
    return None


def build(
    store: RawStore,
    history: dict[tuple[str, str], list[Receipt]],
    dataset: Dataset,
    *,
    since: date,
    today: date,
) -> tuple[pl.DataFrame, Report]:
    """The clean table of a dataset, each month from its last readable response with rows: the
    last publication wins, even with fewer rows.

    When the last response of a month does not read, or is empty, the latest older one with rows
    serves, with a warning, unless the month has left the window, the months that end with the
    last one published: it then keeps the version last published. An error when no last
    response has rows at all, as when the filter no longer matches what Enedis publishes.
    """
    frames = []
    report = Report()
    # The months taken from an older response, judged once the last month published is known.
    older: list[tuple[int, Month, str, Receipt]] = []
    last_published: int | None = None
    # The end of the window: the last month published, or whose last response does not read.
    window_end: int | None = None
    for i, month in enumerate(months(dataset, since, today)):
        receipts = history.get((dataset.kind, month.name), [])
        if not receipts:
            continue
        last = rows_of(store, receipts[-1], month)
        if last is None or last.height:
            window_end = i
        if last is not None and last.height:
            raw, receipt = last, receipts[-1]
            last_published = i
        else:
            found = latest_with_rows(store, receipts[:-1], month)
            if found is None:
                continue
            raw, receipt = found
            older.append((i, month, "is empty" if last is not None else "does not read", receipt))
        frames.append(
            month_values(raw, month).with_columns(
                pl.lit(receipt.received_at, dtype=dataset.schema["received_at"]).alias(
                    "received_at"
                )
            )
        )
    if older and last_published is None:
        report.errors.append(
            f"{dataset.kind}: the last response of every month is empty or unreadable, older "
            "ones are used"
        )
    for i, month, why, receipt in older:
        inside = window_end is not None and i > window_end - dataset.window
        if why == "does not read" or inside:
            report.warnings.append(
                f"{dataset.kind} {month.name}: the last response {why}, the one received at "
                f"{receipt.received_at:%Y-%m-%d %H:%M:%S} UTC is used"
            )
    # Month after month, each sorted within itself: a sort of the whole table would copy it.
    frame = pl.concat(frames, rechunk=False) if frames else dataset.schema.to_frame()
    return frame.select(dataset.schema.names()), report


def parse_publication(content: bytes, url: str) -> datetime:
    """The date of the last publication of a dataset, from its metadata."""
    body = load_json(content, url)
    value = body.get("dataUpdatedAt") if isinstance(body, dict) else None
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
            if moment.utcoffset() is not None:
                return moment.astimezone(UTC)
        except (ValueError, OverflowError):
            pass
    raise SchemaError(f"{url}: no date of publication, dataUpdatedAt {value!r}")


def footer(content: bytes, url: str) -> tuple[int, pl.Schema]:
    """The number of rows and the columns of a Parquet export, read from its footer alone."""
    try:
        scan = pl.scan_parquet(io.BytesIO(content))
        return scan.select(pl.len()).collect().item(), scan.collect_schema()
    # Polars may even panic on a damaged file, and its panic is a BaseException: caught here, it
    # makes the response faulty instead of stopping every run that reads it again.
    except (pl.exceptions.PolarsError, pl.exceptions.PanicException, OSError) as error:
        raise SchemaError(f"{url}: not a Parquet export ({error})") from error


def read_month(content: bytes, month: Month, url: str) -> pl.DataFrame:
    """The rows of a monthly export, as Enedis sends them, after a check of their shape: first
    their number and their columns, from the footer, then the columns Ampère reads."""
    dataset = month.dataset
    rows, schema = footer(content, url)
    if rows > MAX_ROWS or len(schema) > MAX_COLUMNS:
        raise SchemaError(
            f"{url}: {rows} rows in {len(schema)} columns, more than a month of Enedis holds"
        )
    columns = check_columns(schema, dataset, url)
    try:
        raw = pl.read_parquet(io.BytesIO(content), columns=columns)
    except (pl.exceptions.PolarsError, pl.exceptions.PanicException, OSError) as error:
        raise SchemaError(f"{url}: not a Parquet export ({error})") from error
    try:
        check_rows(raw, month, url)
    except (pl.exceptions.PolarsError, pl.exceptions.PanicException) as error:
        raise SchemaError(f"{url}: rows Polars cannot check ({error})") from error
    return raw


def check_rows(raw: pl.DataFrame, month: Month, url: str) -> None:
    """The rows of a monthly export must be in the month, on the half-hour, of the region and
    segments asked, once each, with finite numbers and known indices. A curve is missing exactly
    when its index says that the statistical secrecy masks it, and a total only when the three
    curves are masked."""
    dataset = month.dataset
    sources = [source for source, _ in dataset.segment]
    named = {
        "horodate",
        dataset.sites,
        *sources,
        *(column for column, _ in dataset.allowed),
        *INDICES.values(),
    }
    for column in sorted(named):
        if raw[column].null_count():
            raise SchemaError(f"{url}: a row without {column}")
    if raw.filter(~STARTS.is_between(month.start, month.end, closed="left")).height:
        raise SchemaError(f"{url}: rows outside the month")
    # On the instants as sent: cut to the microsecond, two could become the same half-hour.
    if raw.filter(pl.col("horodate").dt.truncate("30m") != pl.col("horodate")).height:
        raise SchemaError(f"{url}: instants off the half-hour")
    for column, values in dataset.allowed:
        if raw.filter(~pl.col(column).is_in(pl.Series(values).implode())).height:
            raise SchemaError(f"{url}: a {column} other than {', '.join(values)}")
    for column, prefix in dataset.prefixed:
        if raw.filter(~pl.col(column).str.starts_with(prefix)).height:
            raise SchemaError(f"{url}: a {column} that does not start with {prefix}")
    if raw.select(*sources, "horodate").is_duplicated().any():
        raise SchemaError(f"{url}: a half-hour twice for a segment")
    # Finite once doubled into W, as month_values does.
    doubled = pl.col(dataset.total, *CURVES.values()).cast(pl.Float64) * 2
    if raw.select(pl.any_horizontal(~doubled.is_finite()).any()).item():
        raise SchemaError(f"{url}: a value that is not a finite number")
    for curve, index in INDICES.items():
        if raw.filter(~pl.col(index).str.contains(r"^(\d+|< 1|S)$")).height:
            raise SchemaError(f"{url}: an unexpected {index}")
        secret = pl.col(index) == "S"
        if raw.filter(pl.col(curve).is_null() & ~secret).height:
            raise SchemaError(f"{url}: a {curve} missing without {index} at S")
        if raw.filter(pl.col(curve).is_not_null() & secret).height:
            raise SchemaError(f"{url}: a {curve} given with {index} at S")
    all_masked = pl.all_horizontal(pl.col(index) == "S" for index in INDICES.values())
    if raw.filter(pl.col(dataset.total).is_null() & ~all_masked).height:
        raise SchemaError(f"{url}: a {dataset.total} missing where the curves are published")


def month_values(raw: pl.DataFrame, month: Month) -> pl.DataFrame:
    """The values of the checked rows of a month, one per segment, measure and half-hour, with
    their step, sorted in that order; a masked value is left out."""
    dataset = month.dataset
    wide = raw.select(
        STARTS.alias("start"),
        *(pl.col(source).alias(clean) for source, clean in dataset.segment),
        pl.col(dataset.sites).cast(pl.Float64).alias("sites"),
        # In floats: an integer column would overflow once doubled.
        (pl.col(dataset.total).cast(pl.Float64) * 2).alias("total_w"),
        *((pl.col(field).cast(pl.Float64) * 2).alias(name) for name, field in CURVES.items()),
    )
    long = wide.unpivot(
        index=["start", *dataset.keys], variable_name="measure", value_name="value"
    ).drop_nulls("value")
    return (
        with_steps(long, month)
        .with_columns(
            pl.col(dataset.keys).cast(pl.Categorical()),
            pl.col("measure").cast(pl.Enum(MEASURES)),
        )
        .sort(*dataset.keys, "measure", "start")
    )


def check_columns(schema: pl.Schema, dataset: Dataset, url: str) -> list[str]:
    """The columns a monthly export must have, with their types: those Ampère reads."""
    wanted: dict[str, Callable[[pl.DataType], bool]] = {
        "horodate": lambda dtype: isinstance(dtype, pl.Datetime) and dtype.time_zone is None,
        dataset.sites: lambda dtype: dtype.is_integer(),
        dataset.total: lambda dtype: dtype.is_float() or dtype.is_integer(),
        **dict.fromkeys(CURVES.values(), lambda dtype: dtype.is_float() or dtype.is_integer()),
        **dict.fromkeys(
            [column for column, _ in (*dataset.allowed, *dataset.prefixed, *dataset.segment)],
            lambda dtype: dtype == pl.String,
        ),
        **dict.fromkeys(INDICES.values(), lambda dtype: dtype == pl.String),
    }
    for column, fits in wanted.items():
        dtype = schema.get(column)
        if dtype is None or not fits(dtype):
            raise SchemaError(f"{url}: no column {column} of the expected type, but {dtype}")
    return list(wanted)


def with_steps(long: pl.DataFrame, month: Month) -> pl.DataFrame:
    """The step of each value: a day when all the half-hours of a whole Paris day have the same
    value, as the count of sites Enedis gives by day, or the mean of a day that the statistical
    secrecy publishes instead of a curve; a half-hour otherwise. A day at zero keeps the
    half-hour: nothing negative, its half-hours are all known to be zero."""
    days = list(every_day(paris_day(month.start), paris_day(month.end - HALF_HOUR)))
    lengths = pl.DataFrame(
        {"day": days, "half_hours": [quarter_hours(day) // 2 for day in days]},
        schema={"day": pl.Date, "half_hours": pl.UInt32},
    )
    keys = [*month.dataset.keys, "measure", "day"]
    local = long.with_columns(
        pl.col("start").dt.convert_time_zone(PARIS.key).dt.date().alias("day")
    )
    daily = (
        local.group_by(keys)
        .agg(
            pl.col("value").n_unique().alias("distinct"),
            pl.col("value").first().alias("first"),
            pl.len().alias("count"),
        )
        .join(lengths, on="day")
        .select(
            *keys,
            (
                (pl.col("distinct") == 1)
                & (pl.col("count") == pl.col("half_hours"))
                & (pl.col("first") != 0)
            ).alias("daily"),
        )
    )
    return (
        local.join(daily, on=keys, how="left")
        .with_columns(
            pl.when(pl.col("daily"))
            .then(DAY_MINUTES)
            .otherwise(30)
            .cast(pl.UInt16)
            .alias("step_minutes")
        )
        .drop("day", "daily")
    )


def late_months(
    frame: pl.DataFrame, dataset: Dataset, published: datetime | None, *, asked_all: bool = False
) -> list[str]:
    """A warning when the clean table ends more than LAGGING before the last publication: a
    publication covers up to about a month before its date, so that months of it are missing,
    or still those of an earlier one, as after a run cut short. `--full` asks for them again,
    which a run that has just asked for every month cannot do better."""
    last = frame["start"].max()
    if not isinstance(last, datetime) or published is None:
        return []
    if published - (last + HALF_HOUR) <= LAGGING:
        return []
    return [
        f"{dataset.kind}: the clean table ends at {last + HALF_HOUR:%Y-%m-%d %H:%M} UTC, more "
        f"than {LAGGING.days} days before the publication of {published:%Y-%m-%d}"
        + ("" if asked_all else ": run --full")
    ]


def check(
    frame: pl.DataFrame, dataset: Dataset, *, since: date, now: datetime, asked_all: bool = False
) -> Report:
    """Invalid rows; errors for an expected segment never published, and for each half-hour an
    expected segment lacks, known by its count of sites, from the start of the history to the
    last half-hour published. Warnings when that last half-hour is too old or does not end a
    quarter, when the secrecy masks too much of a month, and for a segment not expected. The
    statistical secrecy masks some totals and curves: they are no gaps."""
    report = Report(invalid=invalid_rows(frame, dataset))
    last = frame["start"].max()
    if not isinstance(last, datetime):
        report.errors.append(f"{dataset.kind}: nothing published")
        return report
    end = last + HALF_HOUR
    if now - end > STALE_AFTER:
        report.warnings.append(
            f"{dataset.kind}: the last half-hour published ends at {end:%Y-%m-%d %H:%M} UTC, "
            f"more than {STALE_AFTER.days} days ago"
        )
    # Enedis publishes whole quarters: a table that ends within one lacks the rest of it.
    local = end.astimezone(PARIS)
    if (local.month % 3, local.day, local.hour, local.minute) != (1, 1, 0, 0):
        report.warnings.append(
            f"{dataset.kind}: the last half-hour published ends at {end:%Y-%m-%d %H:%M} UTC, "
            "before the end of its quarter" + ("" if asked_all else ": run --full")
        )
    days = list(every_day(since, paris_day(last)))
    # One count of sites per segment and half-hour: a double is a fault of the shape of its month,
    # and an instant off the grid an invalid row. The streaming engine counts them without a copy
    # of the millions of rows, and the segments come from its few thousand groups.
    paris_date = pl.col("start").dt.convert_time_zone(PARIS.key).dt.date().alias("day")
    grouped = (
        frame.lazy()
        .filter(pl.col("measure") == "sites")
        .group_by(*dataset.keys, paris_date)
        .len()
        .collect(engine="streaming")
    )
    counts = {(*segment, day): count for *segment, day, count in grouped.rows()}
    # Every segment has its counts of sites: check_rows allows none to be missing.
    segments = grouped.select(dataset.keys).unique().sort(dataset.keys).rows()
    # The half-hours of each day, up to the last one published on the last day.
    half_hours = {
        day: (min(day_bounds(day)[1], end) - day_bounds(day)[0]) // HALF_HOUR for day in days
    }
    report.warnings.extend(
        f"{dataset.kind} {' '.join(segment)}: not an expected segment"
        for segment in segments
        if segment not in dataset.expected
    )
    report.warnings.extend(masked_months(frame, dataset))
    # Only the expected segments: one Enedis renames keeps its old name in the months that have
    # left the window, where it would lack every day after them.
    for segment in dataset.expected:
        if segment not in segments:
            report.errors.append(f"{dataset.kind} {' '.join(segment)}: nothing published")
            continue
        for day in days:
            expected = half_hours[day]
            found = counts.get((*segment, day), 0)
            if found < expected:
                report.errors.append(
                    f"{dataset.kind} {' '.join(segment)} {day}: "
                    f"{expected - found} of {expected} half-hours missing"
                )
    return report


def masked_months(frame: pl.DataFrame, dataset: Dataset) -> list[str]:
    """A warning for each Paris month whose global curve the statistical secrecy masks more than
    MASKED_AT_MOST of the time: the share of half-hours with a count of sites but no curve."""
    month = pl.col("start").dt.convert_time_zone(PARIS.key).dt.strftime("%Y-%m").alias("month")
    shares = (
        frame.lazy()
        .group_by("start")
        .agg(
            (pl.col("measure") == "sites").sum().alias("sites"),
            (pl.col("measure") == "mean_w").sum().alias("curves"),
        )
        # About 52,000 instants: the month is only written for each of them.
        .group_by(month)
        .agg(pl.col("sites", "curves").sum())
        .with_columns((1 - pl.col("curves") / pl.col("sites")).alias("masked"))
        .filter(pl.col("masked") > MASKED_AT_MOST)
        .sort("month")
        .collect(engine="streaming")
    )
    return [
        f"{dataset.kind} {month}: the statistical secrecy masks {masked:.1%} of the global curve, "
        f"more than {MASKED_AT_MOST:.0%}"
        for month, _, _, masked in shares.rows()
    ]


def invalid_rows(frame: pl.DataFrame, dataset: Dataset) -> list[str]:
    """The rows a clean file must not hold: an instant off the half-hour, or a value outside the
    limits of its measure.

    A half-hour twice is a fault of the shape of its month (read_month), and two months never
    overlap: looking for doubles among ten million rows would take a gigabyte for nothing.
    """
    keys = [*dataset.keys, "measure", "start"]
    off_grid = frame.filter(pl.col("start").dt.truncate("30m") != pl.col("start")).sort(keys)
    invalid = list(
        f"{start:%Y-%m-%d %H:%M:%S} UTC: {dataset.kind} {' '.join(segment)} {measure} not on "
        "the half-hour"
        for *segment, measure, start in off_grid.select(keys).rows()
    )
    # One lazy pass over the table: only the first rows out of bounds are ever copied, since a
    # faulty publication could make millions of them.
    curve = pl.col("measure").is_in(pl.Series(list(CURVES)).implode())
    outside = frame.lazy().filter(
        (pl.col("value") < 0) | (curve & (pl.col("value") > dataset.highest_mean))
    )
    count = outside.select(pl.len()).collect(engine="streaming").item()
    listed = outside.select(*keys, "value").sort(keys).head(INVALID_LISTED).collect()
    for *segment, measure, start, value in listed.rows():
        limits = f"outside 0 to {dataset.highest_mean:g}" if measure in CURVES else "below 0"
        invalid.append(
            f"{start:%Y-%m-%d %H:%M} UTC: {dataset.kind} {' '.join(segment)} {measure} "
            f"{value:.10g}, {limits}"
        )
    if count > listed.height:
        invalid.append(
            f"{dataset.kind}: {count - listed.height} more values outside their limits, not listed"
        )
    return invalid
