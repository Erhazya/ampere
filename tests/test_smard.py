"""SMARD prices, from a fake SMARD that serves an index and weekly files by URL."""

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import polars as pl
import pytest

from ampere.data.days import day_bounds
from ampere.data.http import client
from ampere.data.raw import RawStore
from ampere.sources.smard import (
    QUARTER_HOURS_FROM,
    Report,
    SchemaError,
    SCHEMA,
    build,
    check,
    ingest,
    parse_index,
    parse_series,
    resolutions,
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
) -> Report:
    with client(httpx2.MockTransport(smard.handle)) as http:
        sleep = pauses.append if pauses is not None else (lambda seconds: None)
        return ingest(http, store, clean, now=now, since=since, sleep=sleep)


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


def test_a_second_run_asks_again_only_for_the_last_two_weeks(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    run(smard, store, clean)
    files = sorted(store.root.rglob("*.gz"))
    first = prices(clean)
    smard.requests.clear()
    assert run(smard, store, clean).errors == []
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
    assert len(asked) == 6  # the index, two missing weeks, and the last two weeks


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
    assert prices(clean).height == 18 * 96 - 1


def test_a_price_outside_the_market_limits_is_an_error(
    smard: FakeSmard, store: RawStore, clean: Path
) -> None:
    smard.week("quarterhour", date(2025, 10, 6), count=4 * 96, price=lambda i: 9000.0)
    errors = run(smard, store, clean).errors
    assert len(errors) == 4 * 96
    assert errors[0] == "2025-10-05 22:00 UTC: 9000.0 €/MWh, outside -500 to 5000"


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


def test_a_damaged_raw_file_is_an_error(smard: FakeSmard, store: RawStore, clean: Path) -> None:
    run(smard, store, clean)
    old = next(r for r in store.receipts("smard") if r.dataset == "prices-hour")
    (store.root / old.path).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="damaged|does not match|Not a gzipped"):
        run(smard, store, clean)


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
    errors = check(
        frame(*rows, (moment, 51.0), (moment + timedelta(minutes=7), 50.0)),
        store,
        since=date(2025, 10, 7),
        now=datetime(2025, 10, 7, 12, tzinfo=PARIS),
    ).errors
    assert f"{moment:%Y-%m-%d %H:%M} UTC: 2 prices" in errors
    assert f"{moment + timedelta(minutes=7):%Y-%m-%d %H:%M:%S} UTC: not on a quarter-hour" in errors


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
