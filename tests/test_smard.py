"""SMARD prices, from a fake SMARD that serves an index and weekly files by URL."""

import json
import logging
import math
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import polars as pl
import pytest

from ampere.data.days import day_bounds
from ampere.data.http import UnexpectedResponse, client
from ampere.data.raw import RawStore
from ampere.sources.smard import (
    QUARTER_HOURS_FROM,
    SCHEMA,
    Report,
    SchemaError,
    build,
    check,
    ingest,
    parse_index,
    parse_series,
    resolutions,
    week_end,
)

PARIS = ZoneInfo("Europe/Paris")
BASE = "https://www.smard.de/app/chart_data/254/DE"
SINCE = date(2025, 9, 22)  # a Monday, when the market still had hourly prices
NOW = datetime(2025, 10, 8, 15, tzinfo=PARIS)  # a Wednesday afternoon


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def hourly(i: int) -> float | None:
    return 40.0 + i % 24


def quarterly(i: int) -> float | None:
    return 100.0 + i % 96 / 4


class FakeSmard:
    """Weekly files as SMARD publishes them, starting on Mondays at midnight in Paris."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.weeks: set[int] = set()
        self.requests: list[str] = []

    def week(
        self,
        resolution: str,
        monday: date,
        count: int | None = None,
        price: Callable[[int], float | None] = hourly,
        series: object = None,
    ) -> None:
        start, end = day_bounds(monday)[0], day_bounds(monday + timedelta(days=7))[0]
        step = timedelta(hours=1) if resolution == "hour" else timedelta(minutes=15)
        count = (end - start) // step if count is None else count
        points = [[ms(start + i * step), price(i)] for i in range(count)]
        body: dict[str, object] = {"meta_data": {"version": 1, "created": 1}, "series": points}
        if series is not None:
            body["series"] = series
        self.files[f"{BASE}/254_DE_{resolution}_{ms(start)}.json"] = json.dumps(body).encode()
        self.weeks.add(ms(start))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        if url == f"{BASE}/index_quarterhour.json":
            return httpx2.Response(200, json={"timestamps": sorted(self.weeks)})
        if url in self.files:
            headers = {"content-type": "application/json"}
            return httpx2.Response(200, content=self.files[url], headers=headers)
        return httpx2.Response(404)


@pytest.fixture
def smard() -> FakeSmard:
    fake = FakeSmard()
    fake.weeks.add(ms(day_bounds(date(2025, 9, 15))[0]))  # a week before the start
    fake.week("hour", date(2025, 9, 22))
    fake.week("hour", date(2025, 9, 29))
    fake.week("quarterhour", date(2025, 9, 29), price=quarterly)
    fake.week("quarterhour", date(2025, 10, 6), count=4 * 96, price=quarterly)  # up to tomorrow
    return fake


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore.create(tmp_path / "raw")


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    return tmp_path / "clean"


def run(
    smard: FakeSmard,
    store: RawStore,
    clean: Path,
    now: datetime = NOW,
    since: date = SINCE,
    pauses: list[float] | None = None,
    full: bool = False,
) -> Report:
    with client(httpx2.MockTransport(smard.handle)) as http:
        sleep = pauses.append if pauses is not None else (lambda seconds: None)
        return ingest(http, store, clean, now=now, since=since, full=full, sleep=sleep)


def prices(clean: Path) -> pl.DataFrame:
    return pl.read_parquet(clean / "smard" / "prices.parquet")


def at(frame: pl.DataFrame, moment: datetime) -> dict[str, object]:
    return frame.filter(pl.col("start") == moment).row(0, named=True)


def test_a_week_needs_the_files_of_its_market_steps() -> None:
    before = datetime(2025, 9, 21, 22, tzinfo=UTC)
    after = datetime(2025, 10, 5, 22, tzinfo=UTC)
    assert resolutions(before, before + timedelta(days=7)) == ["hour"]
    assert resolutions(QUARTER_HOURS_FROM - timedelta(days=2), after) == ["hour", "quarterhour"]
    assert resolutions(after, after + timedelta(days=7)) == ["quarterhour"]


def test_the_first_run_fetches_every_week_since_the_start(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    report = run(smard, store, clean)
    assert report.errors == [] and report.warnings == []
    weeks = [url.removeprefix(f"{BASE}/") for url in smard.requests[1:]]
    assert weeks == [
        f"254_DE_hour_{ms(datetime(2025, 9, 21, 22, tzinfo=UTC))}.json",
        f"254_DE_hour_{ms(datetime(2025, 9, 28, 22, tzinfo=UTC))}.json",
        f"254_DE_quarterhour_{ms(datetime(2025, 9, 28, 22, tzinfo=UTC))}.json",
        f"254_DE_quarterhour_{ms(datetime(2025, 10, 5, 22, tzinfo=UTC))}.json",
    ]
    # Nine days of hourly market, then nine days by the quarter-hour, up to tomorrow evening.
    frame = prices(clean)
    assert frame.height == 18 * 96
    assert frame["start"].min() == day_bounds(SINCE)[0]
    assert frame["start"].max() == day_bounds(date(2025, 10, 9))[1] - timedelta(minutes=15)
    assert dict(frame.schema) == {
        "start": pl.Datetime("us", "UTC"),
        "price_eur_per_mwh": pl.Float64,
        "market_step_minutes": pl.UInt8,
        "received_at": pl.Datetime("us", "UTC"),
    }


def test_an_hourly_price_covers_its_four_quarter_hours(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean)
    hour = datetime(2025, 9, 22, 3, tzinfo=UTC)
    quarters = prices(clean).filter(
        (pl.col("start") >= hour) & (pl.col("start") < hour + timedelta(hours=1))
    )
    assert quarters["price_eur_per_mwh"].to_list() == [hourly(5)] * 4  # the sixth hour of the week
    assert quarters["market_step_minutes"].to_list() == [60] * 4


def test_the_transition_week_takes_each_price_from_its_market_step(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean)
    frame = prices(clean)
    last_hourly = at(frame, QUARTER_HOURS_FROM - timedelta(minutes=15))
    first_quarterly = at(frame, QUARTER_HOURS_FROM)
    assert last_hourly["market_step_minutes"] == 60
    assert last_hourly["price_eur_per_mwh"] == hourly(47)  # 23 h in Paris, on the second day
    assert first_quarterly["market_step_minutes"] == 15
    assert first_quarterly["price_eur_per_mwh"] == quarterly(2 * 96)
    assert frame["start"].is_unique().all()


def test_a_later_run_asks_again_only_for_the_weeks_that_can_still_change(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean)
    files = sorted(store.root.rglob("*.gz"))
    first = prices(clean)
    smard.requests.clear()
    # On 14 October, the week of 22 September ended more than 14 days ago: it is kept for good.
    run(smard, store, clean, now=datetime(2025, 10, 14, 15, tzinfo=PARIS))
    asked = [url.removeprefix(f"{BASE}/") for url in smard.requests]
    assert asked == [
        "index_quarterhour.json",
        f"254_DE_hour_{ms(datetime(2025, 9, 28, 22, tzinfo=UTC))}.json",
        f"254_DE_quarterhour_{ms(datetime(2025, 9, 28, 22, tzinfo=UTC))}.json",
        f"254_DE_quarterhour_{ms(datetime(2025, 10, 5, 22, tzinfo=UTC))}.json",
    ]
    # Nothing new was received: the archive and the clean prices stay as they were.
    assert sorted(store.root.rglob("*.gz")) == files
    assert prices(clean).equals(first)


@pytest.mark.parametrize(
    ("now", "asked"),
    [
        (datetime(2025, 10, 12, 23, 59, tzinfo=PARIS), True),
        (datetime(2025, 10, 13, 0, 0, tzinfo=PARIS), False),
    ],
    ids=["a minute before", "14 days after its end"],
)
def test_a_week_can_change_until_14_days_after_its_end(
    smard: FakeSmard, store: RawStore, clean: Path, now: datetime, asked: bool
) -> None:
    run(smard, store, clean)
    smard.requests.clear()
    run(smard, store, clean, now=now)
    # The week of 22 September ends on the 29th at midnight in Paris.
    week = f"{BASE}/254_DE_hour_{ms(datetime(2025, 9, 21, 22, tzinfo=UTC))}.json"
    assert (week in smard.requests) is asked


def test_full_asks_for_every_week_again(smard: FakeSmard, store: RawStore, clean: Path) -> None:
    run(smard, store, clean)
    smard.requests.clear()
    run(smard, store, clean, now=datetime(2025, 10, 14, 15, tzinfo=PARIS), full=True)
    assert len(smard.requests) == 5  # the index and the four weekly files


def test_each_new_response_and_the_whole_run_are_logged(
    smard: FakeSmard, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ampere")
    run(smard, store, clean)
    kept = [record for record in caplog.records if "new response kept" in record.getMessage()]
    assert len(kept) == 5  # the index and the four weekly files
    assert "smard: 4 weekly files asked, 5 new responses, 1728 quarter-hours" in caplog.text
    caplog.clear()
    run(smard, store, clean)
    assert "smard: 4 weekly files asked, 0 new responses, 1728 quarter-hours" in caplog.text


def test_a_response_larger_than_a_megabyte_is_refused(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    # SMARD's weekly files weigh about 15 kB: anything near a megabyte is not one of them.
    week = f"{BASE}/254_DE_quarterhour_{ms(day_bounds(date(2025, 10, 6))[0])}.json"
    smard.files[week] = b" " * 1_000_001
    with pytest.raises(UnexpectedResponse, match="more than 1000000 bytes"):
        run(smard, store, clean)


JANUARY = date(2026, 1, 5)  # a Monday
LATER = datetime(2026, 2, 6, 15, tzinfo=PARIS)  # a Friday afternoon


def up_to_later(fake: FakeSmard) -> None:
    """What SMARD has published by LATER: four whole weeks from 5 January, then the fifth up to
    Saturday 7 February."""
    for week in range(4):
        fake.week("quarterhour", JANUARY + timedelta(weeks=week), price=quarterly)
    fake.week("quarterhour", JANUARY + timedelta(weeks=4), count=6 * 96, price=quarterly)


def test_a_week_kept_incomplete_is_asked_again_after_its_14_days(
    store: RawStore, clean: Path
) -> None:
    fake = FakeSmard()
    fake.week("quarterhour", JANUARY, count=4 * 96, price=quarterly)  # up to Thursday 8 January
    wednesday = datetime(2026, 1, 7, 15, tzinfo=PARIS)
    assert run(fake, store, clean, since=JANUARY, now=wednesday).errors == []
    # The job does not run for a month; meanwhile SMARD completes the week and adds four more.
    up_to_later(fake)
    fake.requests.clear()
    report = run(fake, store, clean, since=JANUARY, now=LATER)
    assert fake.requests[1] == f"{BASE}/254_DE_quarterhour_{ms(day_bounds(JANUARY)[0])}.json"
    assert report.errors == [] and report.warnings == []
    assert prices(clean).height == 34 * 96  # 5 January to 7 February


def test_an_old_week_received_with_an_unknown_shape_is_asked_again(
    store: RawStore, clean: Path
) -> None:
    fake = FakeSmard()
    up_to_later(fake)
    fake.week("quarterhour", JANUARY, series={"prices": []})
    with pytest.raises(SchemaError):
        run(fake, store, clean, since=JANUARY, now=LATER)
    fake.week("quarterhour", JANUARY, price=quarterly)  # SMARD fixes its file
    report = run(fake, store, clean, since=JANUARY, now=LATER)
    assert report.errors == [] and report.warnings == []


def test_a_damaged_file_of_an_old_week_is_written_again(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean)
    old = next(r for r in store.receipts("smard") if r.dataset == "prices-hour")
    (store.root / old.path).write_bytes(b"damaged")
    smard.requests.clear()
    # The week of 22 September can no longer change, but its kept response no longer reads.
    report = run(smard, store, clean, now=datetime(2025, 10, 14, 15, tzinfo=PARIS))
    assert old.request in smard.requests
    assert store.read(old) == smard.files[old.request]
    assert not [error for error in report.errors if error.startswith("raw layer")]


def test_the_last_week_of_the_index_ends_on_the_next_monday_in_paris() -> None:
    # 26 October 2025 has 25 hours: the week of the 20th lasts 7 days and 1 hour.
    start = datetime(2025, 10, 19, 22, tzinfo=UTC)
    assert week_end(start) == datetime(2025, 10, 26, 23, tzinfo=UTC)


def test_an_earlier_start_fetches_only_the_weeks_missing_from_the_archive(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean, since=date(2025, 9, 29))
    smard.week("hour", date(2025, 9, 15))
    smard.requests.clear()
    run(smard, store, clean, since=date(2025, 9, 15))
    asked = [url.removeprefix(f"{BASE}/") for url in smard.requests]
    assert f"254_DE_hour_{ms(datetime(2025, 9, 14, 22, tzinfo=UTC))}.json" in asked
    assert f"254_DE_hour_{ms(datetime(2025, 9, 21, 22, tzinfo=UTC))}.json" in asked
    assert len(asked) == 6  # the index, the two missing weeks, and three files that can change


def test_the_weeks_are_asked_for_with_a_pause_between_them(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    pauses: list[float] = []
    run(smard, store, clean, pauses=pauses)
    assert pauses == [0.5, 0.5, 0.5]


def test_a_revised_recent_week_replaces_its_prices(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean)
    smard.week("quarterhour", date(2025, 10, 6), count=4 * 96, price=lambda i: 7.5)
    run(smard, store, clean)
    moment = datetime(2025, 10, 7, 10, tzinfo=UTC)
    assert at(prices(clean), moment)["price_eur_per_mwh"] == 7.5
    assert len(store.receipts("smard")) == 6  # the index, four weeks, and the revision


def test_a_missing_quarter_hour_is_an_error_and_stays_missing(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    gap = 96 + 40  # a quarter-hour of Tuesday 7 October
    smard.week(
        "quarterhour",
        date(2025, 10, 6),
        count=4 * 96,
        price=lambda i: None if i == gap else quarterly(i),
    )
    report = run(smard, store, clean)
    assert report.errors == ["2025-10-07: 1 of 96 quarter-hours missing"]
    assert report.invalid == [] and report.failed
    # The gap stays visible in the clean file, which still gets the prices received.
    assert prices(clean).height == 18 * 96 - 1


def test_a_price_outside_the_market_limits_keeps_the_clean_file_as_it_was(
    smard: FakeSmard, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run(smard, store, clean)
    before = (clean / "smard" / "prices.parquet").read_bytes()
    smard.week("quarterhour", date(2025, 10, 6), count=4 * 96, price=lambda i: 9000.0)
    report = run(smard, store, clean)
    assert len(report.invalid) == 4 * 96 and report.failed
    assert report.invalid[0] == "2025-10-05 22:00 UTC: 9000.0 €/MWh, outside -500 to 5000"
    assert (clean / "smard" / "prices.parquet").read_bytes() == before
    assert "prices.parquet kept as it was" in caplog.text


def test_a_failed_write_keeps_the_last_clean_file_whole(
    smard: FakeSmard, store: RawStore, clean: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(smard, store, clean)
    folder = clean / "smard"
    before = (folder / "prices.parquet").read_bytes()
    write = pl.DataFrame.write_parquet

    def fail_halfway(frame: pl.DataFrame, file: Any, **options: Any) -> None:
        write(frame.head(10), file, **options)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", fail_halfway)
    with pytest.raises(OSError, match="No space left"):
        run(smard, store, clean)
    assert (folder / "prices.parquet").read_bytes() == before
    assert [file.name for file in folder.iterdir()] == ["prices.parquet"]


@pytest.mark.parametrize(("hour", "warned"), [(15, True), (14, True), (13, False)])
def test_prices_missing_for_tomorrow_are_a_warning_in_the_afternoon(
    smard: FakeSmard, store: RawStore, clean: Path, hour: int, warned: bool
) -> None:
    smard.week("quarterhour", date(2025, 10, 6), count=3 * 96, price=quarterly)  # up to today
    report = run(smard, store, clean, now=datetime(2025, 10, 8, hour, tzinfo=PARIS))
    assert report.errors == []
    expected = ["2025-10-09: 0 of 96 quarter-hours published so far"] if warned else []
    assert report.warnings == expected


def test_a_day_with_a_clock_change_expects_its_own_count(store: RawStore, clean: Path) -> None:
    fake = FakeSmard()
    fake.week("quarterhour", date(2026, 3, 23), price=quarterly)  # 29 March has 92 quarter-hours
    report = run(
        fake, store, clean, since=date(2026, 3, 23), now=datetime(2026, 3, 29, 12, tzinfo=PARIS)
    )
    assert report.errors == [] and report.warnings == []
    assert prices(clean).height == 6 * 96 + 92


def test_an_unexpected_shape_stops_with_its_url_and_stays_in_the_archive(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    smard.week("quarterhour", date(2025, 10, 6), series={"prices": []})
    with pytest.raises(SchemaError, match="254_DE_quarterhour_"):
        run(smard, store, clean)
    assert any(
        receipt.request.endswith(f"{ms(datetime(2025, 10, 5, 22, tzinfo=UTC))}.json")
        for receipt in store.receipts("smard")
    )


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"meta_data": {"version": 2}, "series": []}',
        b'{"meta_data": {"version": 1}, "series": [[1759096800000]]}',
        b'{"meta_data": {"version": 1}, "series": [["1759096800000", 51.6]]}',
        b'{"meta_data": {"version": 1}, "series": [[1759096800000, "51.6"]]}',
        b'{"meta_data": {"version": 1}, "series": [[1759096800000, true]]}',
    ],
)
def test_a_series_must_keep_its_known_shape(body: bytes) -> None:
    with pytest.raises(SchemaError, match=re.escape("https://example.test/week.json")):
        parse_series(body, "https://example.test/week.json")


def test_a_null_price_is_an_absent_price() -> None:
    body = b'{"meta_data": {"version": 1}, "series": [[1759096800000, null], [1759100400000, -12]]}'
    assert parse_series(body, "https://example.test/week.json") == [
        (1759096800000, None),
        (1759100400000, -12.0),
    ]


def test_the_clean_prices_come_from_the_archive_alone(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean)
    assert build(RawStore(store.root), since=SINCE).equals(prices(clean))


def test_a_first_day_in_the_middle_of_a_week_keeps_only_its_days(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean, since=date(2025, 9, 24))
    assert f"{BASE}/254_DE_hour_{ms(datetime(2025, 9, 21, 22, tzinfo=UTC))}.json" in smard.requests
    frame = prices(clean)
    assert frame["start"].min() == day_bounds(date(2025, 9, 24))[0]
    assert frame.height == 16 * 96


def test_a_gap_today_is_an_error_too(smard: FakeSmard, store: RawStore, clean: Path) -> None:
    today = 2 * 96 + 10  # a quarter-hour of Wednesday 8 October
    smard.week(
        "quarterhour",
        date(2025, 10, 6),
        count=4 * 96,
        price=lambda i: None if i == today else quarterly(i),
    )
    assert run(smard, store, clean).errors == ["2025-10-08: 1 of 96 quarter-hours missing"]


def frame(*rows: tuple[datetime, float]) -> pl.DataFrame:
    received = datetime(2025, 10, 8, tzinfo=UTC)
    return pl.DataFrame(
        [(start, price, 15, received) for start, price in rows], schema=SCHEMA, orient="row"
    )


def day(first: date, price: float = 50.0) -> list[tuple[datetime, float]]:
    start = day_bounds(first)[0]
    return [(start + timedelta(minutes=15 * i), price) for i in range(96)]


def test_the_checks_report_duplicates_and_quarter_hours_off_the_grid(store: RawStore) -> None:
    rows = day(date(2025, 10, 7))
    moment = rows[5][0]
    invalid = check(
        frame(*rows, (moment, 51.0), (moment + timedelta(minutes=7), 50.0)),
        store,
        since=date(2025, 10, 7),
        now=datetime(2025, 10, 7, 12, tzinfo=PARIS),
    ).invalid
    assert f"{moment:%Y-%m-%d %H:%M} UTC: 2 prices" in invalid
    assert (
        f"{moment + timedelta(minutes=7):%Y-%m-%d %H:%M:%S} UTC: not on a quarter-hour" in invalid
    )


def test_duplicates_are_reported_in_time_order(store: RawStore) -> None:
    rows = day(date(2025, 10, 7))
    report = check(
        frame(*rows, *rows[90:10:-8]),
        store,
        since=date(2025, 10, 7),
        now=datetime(2025, 10, 7, 12, tzinfo=PARIS),
    )
    assert len(report.invalid) == 10
    assert report.invalid == sorted(report.invalid)


def test_an_instant_off_the_grid_does_not_hide_a_missing_quarter_hour(store: RawStore) -> None:
    rows = day(date(2025, 10, 7))
    moved = rows[40][0] + timedelta(minutes=7)
    report = check(
        frame(*rows[:40], (moved, 50.0), *rows[41:]),
        store,
        since=date(2025, 10, 7),
        now=datetime(2025, 10, 7, 12, tzinfo=PARIS),
    )
    assert report.invalid == [f"{moved:%Y-%m-%d %H:%M:%S} UTC: not on a quarter-hour"]
    assert report.errors == ["2025-10-07: 1 of 96 quarter-hours missing"]


def test_a_price_after_tomorrow_is_invalid(store: RawStore) -> None:
    # On 7 October, the market has set the prices of the 8th, not those of the 9th.
    rows = day(date(2025, 10, 7)) + day(date(2025, 10, 8)) + day(date(2025, 10, 9))[:1]
    report = check(
        frame(*rows), store, since=date(2025, 10, 7), now=datetime(2025, 10, 7, 15, tzinfo=PARIS)
    )
    assert report.invalid == ["2025-10-08 22:00 UTC: a price after tomorrow"]


@pytest.mark.parametrize(
    ("price", "valid"),
    [(-500.0, True), (5000.0, True), (-500.01, False), (5000.01, False), (math.nan, False)],
    ids=str,
)
def test_the_market_limits_themselves_are_valid_prices(
    store: RawStore, price: float, valid: bool
) -> None:
    rows = day(date(2025, 10, 7))
    rows[10] = (rows[10][0], price)
    report = check(
        frame(*rows), store, since=date(2025, 10, 7), now=datetime(2025, 10, 7, 12, tzinfo=PARIS)
    )
    assert (report.invalid == []) is valid


def test_a_duplicate_does_not_hide_a_missing_quarter_hour(store: RawStore) -> None:
    rows = day(date(2025, 10, 7))
    duplicated = [*rows[:40], rows[39], *rows[41:]]  # the 41st quarter-hour replaced by a copy
    errors = check(
        frame(*duplicated),
        store,
        since=date(2025, 10, 7),
        now=datetime(2025, 10, 7, 12, tzinfo=PARIS),
    ).errors
    assert "2025-10-07: 1 of 96 quarter-hours missing" in errors


def test_a_raw_layer_problem_is_a_check_error(store: RawStore) -> None:
    (store.root / "smard").mkdir()
    (store.root / "smard" / "manifest.jsonl").write_text("")
    (store.root / "smard" / "stray.json.gz").write_bytes(b"x")
    errors = check(
        frame(*day(date(2025, 10, 7))),
        store,
        since=date(2025, 10, 7),
        now=datetime(2025, 10, 7, 12, tzinfo=PARIS),
    ).errors
    assert errors == ["raw layer: smard/stray.json.gz: not in the manifest"]


@pytest.mark.parametrize(
    "body",
    [b"[]", b"{}", b'{"timestamps": []}', b'{"timestamps": ["1759096800000"]}', b"not json"],
)
def test_an_index_must_list_the_week_starts(body: bytes) -> None:
    with pytest.raises(SchemaError, match=re.escape("https://example.test/index.json")):
        parse_index(body, "https://example.test/index.json")


def test_the_index_is_read_oldest_first() -> None:
    assert parse_index(b'{"timestamps": [3, 1, 2]}', "https://example.test/index.json") == [1, 2, 3]
