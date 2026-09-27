"""Enedis: the half-hourly consumption of the homes of Auvergne-Rhône-Alpes, and the injection of
rooftop solar, segment by segment (ADR 026).

Enedis publishes a three-year window every quarter. Each run reads the date of the last
publication of both datasets in the metadata of Enedis's native API. For 7 days after a new
publication, and whenever the last response of a month does not read, it asks for the Paris
months from July 2023 to the current one, one Parquet export per month, through the compatibility
layer of the old API. It keeps every response in the raw layer, rebuilds
clean/enedis/consumption.parquet and clean/enedis/solar.parquet from the most complete response of
each month, and checks them.
"""

import io
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx2
import polars as pl

from ampere.data.clean import Report, write_parquet
from ampere.data.days import PARIS, every_day, paris_day, paris_months, quarter_hours
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
# After a new publication, every month is asked again for this long: a run cut short leaves no
# month at the publication before.
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
# a large segment, or "S" when the curve is masked.
# The instants of an export: UTC, although Parquet gives them without a time zone.
STARTS = pl.col("horodate").dt.cast_time_unit("us").dt.replace_time_zone("UTC")
INDICES = (
    "indice_representativite_courbe_ndeg1_ndeg2",
    "indice_representativite_courbe_ndeg1",
    "indice_representativite_courbe_ndeg2",
)


@dataclass(frozen=True)
class Dataset:
    """A dataset of Enedis as Ampère takes it: its name there and in the raw and clean layers, the
    filter of its rows and the values it allows, its segment columns with their clean names, its
    count and total columns, and the most a mean curve can be, in W."""

    name: str
    kind: str
    where: str
    allowed: tuple[tuple[str, tuple[str, ...]], ...]
    prefixed: tuple[tuple[str, str], ...]
    segment: tuple[tuple[str, str], ...]
    sites: str
    total: str
    highest_mean: float

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


CONSUMPTION = Dataset(
    "conso-inf36-region",
    "consumption",
    "code_region='84' and startswith(profil, 'RES')",
    (("code_region", ("84",)),),
    (("profil", "RES"),),
    (("profil", "profile"), ("plage_de_puissance_souscrite", "power_range")),
    "nb_points_soutirage",
    "total_energie_soutiree_wh",
    36_000,
)
ROOFTOPS = ("P1 : ]0 - 3] kW", "P2 : ]3 - 9] kW")
SOLAR = Dataset(
    "prod-region",
    "solar",
    "code_region='84' and filiere_de_production='F5 : Solaire' and "
    f"plage_de_puissance_injection in ('{ROOFTOPS[0]}', '{ROOFTOPS[1]}')",
    (
        ("code_region", ("84",)),
        ("filiere_de_production", ("F5 : Solaire",)),
        ("plage_de_puissance_injection", ROOFTOPS),
    ),
    (),
    (("plage_de_puissance_injection", "power_range"),),
    "nb_points_injection",
    "total_energie_injectee_wh",
    9_000,
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
) -> Report:
    """Read the date of the last publication of each dataset, fetch its months when it is new, or
    those whose last response does not read, every month if full; then rebuild each clean file
    from the most complete response of each month, check it, and replace it if its rows are
    valid."""
    kept = len(store.receipts(SOURCE))
    report = Report()
    publications: dict[str, datetime] = {}
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

    for dataset in DATASETS:
        content, receipt = ask(PUBLICATION, dataset.name, metadata_url(dataset), "json")
        refresh = full
        try:
            published = publications[dataset.name] = parse_publication(content, receipt.url)
        except SchemaError as error:
            # Without the date of the publication, only the months without a readable response
            # are asked for.
            report.errors.append(str(error))
        else:
            # A publication seen to replace another opens the window at its first reception;
            # the first one ever seen, at its own date.
            opened = first_reception(store, dataset, published) or published
            if now < opened + REFRESH:
                refresh = True
                log.info("%s: publication of %s, every month asked", dataset.name, published)
        history = responses(store)
        for month in months(dataset, since, paris_day(now)):
            receipts = history.get((dataset.kind, month.name), [])
            if refresh or not receipts or not readable(store, receipts[-1], month):
                content, receipt = ask(dataset.kind, month.name, month.url, "parquet")
                try:
                    read_month(content, month, receipt.url)
                except SchemaError as error:
                    # A faulty month is an error; the run goes on with what the raw layer has.
                    report.errors.append(str(error))
    history = responses(store)
    for dataset in DATASETS:
        frame, warnings = build(store, history, dataset, since=since, today=paris_day(now))
        report.warnings.extend(warnings)
        report.warnings.extend(late_months(frame, dataset, publications.get(dataset.name)))
        found = check(frame, dataset, since=since, now=now)
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


def readable(store: RawStore, receipt: Receipt, month: Month) -> bool:
    """Whether a kept response of a month still reads, logging why when it does not."""
    try:
        read_month(store.read(receipt), month, receipt.url)
    except (DamagedRawFile, SchemaError) as error:
        log.warning(
            "%s %s: the last response is unusable (%s)", month.dataset.kind, month.name, error
        )
        return False
    return True


def build(
    store: RawStore,
    history: dict[tuple[str, str], list[Receipt]],
    dataset: Dataset,
    *,
    since: date,
    today: date,
) -> tuple[pl.DataFrame, list[str]]:
    """The clean table of a dataset, month by month from its most complete readable response,
    the latest one among equals: a month Enedis no longer publishes comes back empty, and keeps
    the version it last published. A warning says when a month comes from an older response
    because its last one is cut short or does not read."""
    frames = []
    warnings = []
    for month in months(dataset, since, today):
        receipts = history.get((dataset.kind, month.name), [])
        # The rows of each response, from its footer: only the one chosen is read whole.
        footers: list[tuple[int, int, Receipt, bytes]] = []
        for order, receipt in enumerate(receipts):
            try:
                content = store.read(receipt)
                footers.append((footer(content, receipt.url)[0], order, receipt, content))
            except (DamagedRawFile, SchemaError):
                continue
        last_rows = next((rows for rows, _, kept, _ in footers if kept == receipts[-1]), None)
        best: tuple[pl.DataFrame, Receipt] | None = None
        for _, _, receipt, content in sorted(footers, key=lambda found: found[:2], reverse=True):
            try:
                best = read_month(content, month, receipt.url), receipt
                break
            except SchemaError:
                continue
        if best is None:
            continue
        raw, receipt = best
        if receipt != receipts[-1] and last_rows != 0:
            warnings.append(
                f"{dataset.kind} {month.name}: the response received at "
                f"{receipt.received_at:%Y-%m-%d %H:%M:%S} UTC is used, the last one being cut "
                "short or unreadable"
            )
        frames.append(
            month_values(raw, month).with_columns(
                pl.lit(receipt.received_at, dtype=dataset.schema["received_at"]).alias(
                    "received_at"
                )
            )
        )
    # Month after month, each sorted within itself: a sort of the whole table would copy it.
    frame = pl.concat(frames, rechunk=False) if frames else dataset.schema.to_frame()
    return frame.select(dataset.schema.names()), warnings


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


def parse_month(content: bytes, month: Month, url: str) -> pl.DataFrame:
    """The values of a monthly export, in the long form of the clean table but for the reception
    time; none when Enedis does not publish the month."""
    return month_values(read_month(content, month, url), month)


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
    segments asked, once each, with known indices and finite numbers."""
    dataset = month.dataset
    sources = [source for source, _ in dataset.segment]
    named = {"horodate", *sources, *(column for column, _ in dataset.allowed)}
    for column in sorted(named):
        if raw[column].null_count():
            raise SchemaError(f"{url}: a row without {column}")
    if raw.filter(~STARTS.is_between(month.start, month.end, closed="left")).height:
        raise SchemaError(f"{url}: rows outside the month")
    if raw.filter(STARTS.dt.truncate("30m") != STARTS).height:
        raise SchemaError(f"{url}: instants off the half-hour")
    for column, values in dataset.allowed:
        if raw.filter(~pl.col(column).is_in(pl.Series(values).implode())).height:
            raise SchemaError(f"{url}: a {column} other than {', '.join(values)}")
    for column, prefix in dataset.prefixed:
        if raw.filter(~pl.col(column).str.starts_with(prefix)).height:
            raise SchemaError(f"{url}: a {column} that does not start with {prefix}")
    if raw.select(*sources, "horodate").is_duplicated().any():
        raise SchemaError(f"{url}: a half-hour twice for a segment")
    for column in INDICES:
        if raw.filter(
            pl.col(column).is_not_null() & ~pl.col(column).str.contains(r"^(\d+|< 1|S)$")
        ).height:
            raise SchemaError(f"{url}: an unexpected {column}")
    numbers = [dataset.total, *CURVES.values()]
    if raw.select(pl.any_horizontal(~pl.col(numbers).is_finite()).any()).item():
        raise SchemaError(f"{url}: a value that is not a finite number")


def month_values(raw: pl.DataFrame, month: Month) -> pl.DataFrame:
    """The values of the checked rows of a month, one per segment, measure and half-hour, with
    their step, sorted in that order; a masked value is left out."""
    dataset = month.dataset
    wide = raw.select(
        STARTS.alias("start"),
        *(pl.col(source).alias(clean) for source, clean in dataset.segment),
        pl.col(dataset.sites).cast(pl.Float64).alias("sites"),
        (pl.col(dataset.total) * 2).alias("total_w"),
        *((pl.col(field) * 2).alias(name) for name, field in CURVES.items()),
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
            {column for column, _ in (*dataset.allowed, *dataset.prefixed, *dataset.segment)},
            lambda dtype: dtype == pl.String,
        ),
        **dict.fromkeys(INDICES, lambda dtype: dtype == pl.String),
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


def late_months(frame: pl.DataFrame, dataset: Dataset, published: datetime | None) -> list[str]:
    """A warning when the clean table ends more than LAGGING before the last publication: a
    publication covers up to about a month before its date, so that months of it are missing,
    or still those of an earlier one, as after a run cut short. `--full` asks for them again."""
    last = frame["start"].max()
    if published is None or not isinstance(last, datetime) or published - last <= LAGGING:
        return []
    return [
        f"{dataset.kind}: the clean table ends at {last + HALF_HOUR:%Y-%m-%d %H:%M} UTC, more "
        f"than {LAGGING.days} days before the publication of {published:%Y-%m-%d}: run --full"
    ]


def check(frame: pl.DataFrame, dataset: Dataset, *, since: date, now: datetime) -> Report:
    """Invalid rows; errors for each half-hour a segment lacks, known by its count of sites, from
    the start of the history to the last half-hour published; a warning when that last half-hour
    is too old. The statistical secrecy masks some totals and curves: they are no gaps."""
    report = Report(invalid=invalid_rows(frame, dataset))
    last = frame["start"].max()
    if not isinstance(last, datetime):
        report.warnings.append(f"{dataset.kind}: nothing published")
        return report
    if now - (last + HALF_HOUR) > STALE_AFTER:
        report.warnings.append(
            f"{dataset.kind}: the last half-hour published ends at "
            f"{last + HALF_HOUR:%Y-%m-%d %H:%M} UTC, more than {STALE_AFTER.days} days ago"
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
    segments = (
        frame.lazy()
        .select(dataset.keys)
        .unique()
        .collect(engine="streaming")
        .sort(dataset.keys)
        .rows()
    )
    for segment in segments:
        for day in days:
            expected = quarter_hours(day) // 2
            found = counts.get((*segment, day), 0)
            if found < expected:
                report.errors.append(
                    f"{dataset.kind} {' '.join(segment)} {day}: "
                    f"{expected - found} of {expected} half-hours missing"
                )
    return report


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
    # One lazy pass over the table: only the rows out of bounds are ever copied.
    curve = pl.col("measure").is_in(pl.Series(list(CURVES)).implode())
    outside = (
        frame.lazy()
        .filter((pl.col("value") < 0) | (curve & (pl.col("value") > dataset.highest_mean)))
        .select(*keys, "value")
        .sort(keys)
        .collect()
    )
    for *segment, measure, start, value in outside.rows():
        limits = f"outside 0 to {dataset.highest_mean:g}" if measure in CURVES else "below 0"
        invalid.append(
            f"{start:%Y-%m-%d %H:%M} UTC: {dataset.kind} {' '.join(segment)} {measure} "
            f"{value:g}, {limits}"
        )
    return invalid
