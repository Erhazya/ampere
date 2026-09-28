"""The calendars against a fake of their two sources: the public holidays of Etalab's API, and the
school calendar of the ministry of Education as a Parquet export."""

import io
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx2
import polars as pl
import pytest

from ampere.data.clean import Report
from ampere.data.days import day_bounds
from ampere.data.http import client
from ampere.data.raw import RawStore
from ampere.sources.calendars import (
    HOLIDAYS_RESOURCE,
    HOLIDAYS_UPDATES_URL,
    HOLIDAYS_URL,
    SCHEMA,
    SCHOOL_UPDATES_URL,
    SCHOOL_URL,
    Period,
    check,
    ingest,
    parse_holidays,
    parse_school,
)
from ampere.sources.shapes import SchemaError

SINCE = date(2025, 7, 1)
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
FIXED = {
    "01-01": "1er janvier",
    "05-01": "1er mai",
    "05-08": "8 mai",
    "07-14": "14 juillet",
    "08-15": "Assomption",
    "11-01": "Toussaint",
    "11-11": "11 novembre",
    "12-25": "Jour de Noël",
}
MOVABLE = {
    2024: {"04-01": "Lundi de Pâques", "05-09": "Ascension", "05-20": "Lundi de Pentecôte"},
    2025: {"04-21": "Lundi de Pâques", "05-29": "Ascension", "06-09": "Lundi de Pentecôte"},
    2026: {"04-06": "Lundi de Pâques", "05-14": "Ascension", "05-25": "Lundi de Pentecôte"},
    2027: {"03-29": "Lundi de Pâques", "05-06": "Ascension", "05-17": "Lundi de Pentecôte"},
    2028: {"04-17": "Lundi de Pâques", "05-25": "Ascension", "06-05": "Lundi de Pentecôte"},
}
# The school holidays of Lyon as the ministry gives them: from the first day without classes to
# the day they resume, both at midnight in Paris; a single day starts and ends at the same time.
LYON = [
    ("2024-2025", "Vacances d'Été", "Élèves", date(2025, 7, 5), date(2025, 9, 1)),
    ("2024-2025", "Vacances d'Été", "Enseignants", date(2025, 7, 5), date(2025, 8, 29)),
    ("2025-2026", "Vacances de la Toussaint", "-", date(2025, 10, 18), date(2025, 11, 3)),
    ("2025-2026", "Vacances de Noël", "-", date(2025, 12, 20), date(2026, 1, 5)),
    ("2025-2026", "Vacances d'Hiver", "-", date(2026, 2, 7), date(2026, 2, 23)),
    ("2025-2026", "Vacances de Printemps", "-", date(2026, 4, 4), date(2026, 4, 20)),
    ("2025-2026", "Pont de l'Ascension", "-", date(2026, 5, 14), date(2026, 5, 18)),
    ("2025-2026", "Vacances d'Été", "Élèves", date(2026, 7, 4), date(2026, 9, 1)),
    ("2025-2026", "Vacances d'Été", "Enseignants", date(2026, 7, 4), date(2026, 8, 31)),
    ("2026-2027", "Vacances de la Toussaint", "-", date(2026, 10, 17), date(2026, 11, 2)),
    ("2026-2027", "Vacances de Noël", "-", date(2026, 12, 19), date(2027, 1, 4)),
    ("2026-2027", "Vacances d'Hiver", "-", date(2027, 2, 13), date(2027, 3, 1)),
    ("2026-2027", "Vacances de Printemps", "-", date(2027, 4, 10), date(2027, 4, 26)),
    ("2026-2027", "Pont de l'Ascension", "-", date(2027, 5, 7), date(2027, 5, 7)),
    ("2026-2027", "Vacances d'Été", "Élèves", date(2027, 7, 3), date(2027, 9, 2)),
    ("2026-2027", "Vacances d'Été", "Enseignants", date(2027, 7, 3), date(2027, 9, 1)),
    ("2027-2028", "Vacances de la Toussaint", "-", date(2027, 10, 23), date(2027, 11, 8)),
    ("2027-2028", "Vacances de Noël", "-", date(2027, 12, 18), date(2028, 1, 3)),
    # The end of the last summer is not published yet: only its first day.
    ("2027-2028", "Début des Vacances d'Été", "-", date(2028, 7, 4), date(2028, 7, 4)),
]


def midnight(day: date) -> datetime:
    return day_bounds(day)[0]


def school_rows(
    periods: list[tuple[str, str, str, date, date]] = LYON,
) -> list[dict[str, object]]:
    """The rows of the whole calendar: Lyon and another academy of zone A share the periods; one
    of zone B has its winter a week later."""
    rows: list[dict[str, object]] = []
    for location, zone, shift in (
        ("Lyon", "Zone A", 0),
        ("Grenoble", "Zone A", 0),
        ("Rennes", "Zone B", 7),
    ):
        for year, name, population, start, end in periods:
            moved = timedelta(days=shift if name == "Vacances d'Hiver" else 0)
            rows.append(
                {
                    "description": name,
                    "population": population,
                    "start_date": midnight(start + moved),
                    "end_date": midnight(end + moved),
                    "location": location,
                    "zones": zone,
                    "annee_scolaire": year,
                }
            )
    return rows


def school_export(rows: list[dict[str, object]], drop: str | None = None) -> bytes:
    """A Parquet export with the columns of the ministry, as it names and types them."""
    table = pl.DataFrame(
        rows,
        schema={
            "description": pl.String(),
            "population": pl.String(),
            "start_date": pl.Datetime("ms", "UTC"),
            "end_date": pl.Datetime("ms", "UTC"),
            "location": pl.String(),
            "zones": pl.String(),
            "annee_scolaire": pl.String(),
        },
    )
    if drop is not None:
        table = table.drop(drop)
    buffer = io.BytesIO()
    table.write_parquet(buffer)
    return buffer.getvalue()


def holidays_json(years: range = range(2025, 2028)) -> bytes:
    return json.dumps(
        {
            f"{year}-{day}": name
            for year in years
            for day, name in sorted({**FIXED, **MOVABLE[year]}.items())
        }
    ).encode()


class FakeCalendars:
    """Both sources: the same content gives the same bytes, as they do."""

    def __init__(self) -> None:
        self.now = NOW
        self.holidays = holidays_json()
        self.school = school_export(school_rows())
        # The dates each source publishes for its last update, as they stood on 28 September 2026.
        self.school_updated: object = "2026-09-18T08:35:17.505000+00:00"
        self.holidays_updated: object = "2026-09-27T18:33:16+00:00"
        self.bodies: dict[str, bytes] = {}
        self.requests: list[str] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.bodies:
            return httpx2.Response(
                200, content=self.bodies[url], headers={"content-type": "application/json"}
            )
        if url == SCHOOL_UPDATES_URL:
            row = {"dataset_id": "fr-en-calendrier-scolaire", "data_processed": self.school_updated}
            return httpx2.Response(200, json={"total_count": 1, "results": [row]})
        if url == HOLIDAYS_UPDATES_URL:
            resources = [
                {
                    "url": "https://etalab.github.io/jours-feries-france-data/csv/jours_feries_metropole.csv",
                    "last_modified": "2026-01-02T14:32:45+00:00",
                },
                {"url": HOLIDAYS_RESOURCE, "last_modified": self.holidays_updated},
            ]
            return httpx2.Response(
                200, json={"title": "Jours fériés en France", "resources": resources}
            )
        if url == HOLIDAYS_URL:
            return httpx2.Response(
                200, content=self.holidays, headers={"content-type": "application/json"}
            )
        if url == SCHOOL_URL:
            return httpx2.Response(
                200,
                content=self.school,
                headers={"content-type": "application/parquet; charset=utf-8"},
            )
        return httpx2.Response(404)


@pytest.fixture
def fake() -> FakeCalendars:
    return FakeCalendars()


@pytest.fixture
def store(tmp_path: Path, fake: FakeCalendars) -> RawStore:
    return RawStore.create(tmp_path / "raw", clock=lambda: fake.now)


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    return tmp_path / "clean"


def run(
    fake: FakeCalendars,
    store: RawStore,
    clean: Path,
    now: datetime = NOW,
    pauses: list[float] | None = None,
) -> Report:
    fake.now = now
    with client(httpx2.MockTransport(fake.handle)) as http:
        sleep = pauses.append if pauses is not None else (lambda seconds: None)
        return ingest(http, store, clean, now=now, since=SINCE, sleep=sleep)


def days(clean: Path) -> pl.DataFrame:
    return pl.read_parquet(clean / "calendars" / "days.parquet")


def on(frame: pl.DataFrame, day: date) -> tuple[object, object]:
    (row,) = frame.filter(pl.col("day") == day).select("public_holiday", "school_holidays").rows()
    return row


def test_each_run_asks_for_both_calendars_whole(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    pauses: list[float] = []
    assert run(fake, store, clean, pauses=pauses) == Report()
    assert fake.requests == [HOLIDAYS_URL, SCHOOL_URL, SCHOOL_UPDATES_URL, HOLIDAYS_UPDATES_URL]
    assert pauses == [0.5] * 3
    receipts = store.receipts("calendars")
    assert [(r.dataset, r.request) for r in receipts] == [
        ("public-holidays", "metropole"),
        ("school-holidays", "fr-en-calendrier-scolaire"),
        ("updates", "school-holidays"),
        ("updates", "public-holidays"),
    ]
    assert receipts[0].path.endswith(".json.gz")
    assert receipts[1].path.endswith(".parquet.gz")


def test_the_same_calendars_add_nothing_to_the_raw_layer(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.requests.clear()
    assert run(fake, store, clean, now=NOW + timedelta(days=1)) == Report()
    assert len(fake.requests) == 4
    assert len(store.receipts("calendars")) == 4


def test_the_days_hold_the_holidays_of_the_pupils_of_lyon(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    table = days(clean)
    assert table.schema == SCHEMA
    assert table["day"].to_list() == [
        SINCE + timedelta(days=i) for i in range((date(2027, 12, 31) - SINCE).days + 1)
    ]
    # Classes go on until the summer starts, on 5 July; the pupils go back on 1 September, a
    # school day, and the teachers two days before.
    assert on(table, date(2025, 7, 4)) == (None, None)
    assert on(table, date(2025, 7, 14)) == ("14 juillet", "Vacances d'Été")
    assert on(table, date(2025, 8, 31)) == (None, "Vacances d'Été")
    assert on(table, date(2025, 9, 1)) == (None, None)
    # The winter of Lyon, not that of zone B, a week later.
    assert on(table, date(2026, 2, 22)) == (None, "Vacances d'Hiver")
    assert on(table, date(2026, 2, 23)) == (None, None)
    # A period of a single day: the Friday after Ascension, 2027.
    assert on(table, date(2027, 5, 6)) == ("Ascension", None)
    assert on(table, date(2027, 5, 7)) == (None, "Pont de l'Ascension")
    assert on(table, date(2027, 5, 8)) == ("8 mai", None)
    received = table.select("public_holiday_received_at", "school_holidays_received_at")
    assert received.unique().rows() == [(NOW, NOW)]


def test_the_days_end_on_the_last_one_both_calendars_know(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    # The public holidays now reach 2028: the school calendar stops first, on the first day of
    # the summer of 2028, the end of which is not published yet.
    fake.holidays = holidays_json(range(2025, 2029))
    run(fake, store, clean)
    table = days(clean)
    assert table["day"].max() == date(2028, 7, 4)
    assert on(table, date(2028, 7, 4)) == (None, "Début des Vacances d'Été")


def test_a_faulty_calendar_is_an_error_and_the_one_before_serves(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.holidays = b"<html>"
    later = NOW + timedelta(days=1)
    report = run(fake, store, clean, now=later)
    assert len(report.errors) == 1
    assert report.errors[0].startswith(f"{HOLIDAYS_URL}: not JSON")
    table = days(clean)
    assert on(table, date(2025, 11, 1)) == ("Toussaint", "Vacances de la Toussaint")
    assert table["public_holiday_received_at"].unique().to_list() == [NOW]


def test_each_calendar_keeps_the_reception_time_of_its_response(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    # A day later, the ministry publishes a new version; the public holidays stay the same.
    fake.school = school_export(school_rows([p for p in LYON if p[1] != "Pont de l'Ascension"]))
    later = NOW + timedelta(days=1)
    run(fake, store, clean, now=later)
    received = days(clean).select("public_holiday_received_at", "school_holidays_received_at")
    assert received.unique().rows() == [(NOW, later)]


def test_only_the_last_faulty_response_is_an_error(
    fake: FakeCalendars, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Two faulty responses after a good one: the older of them is only logged.
    run(fake, store, clean)
    fake.holidays = b"<html>"
    run(fake, store, clean, now=NOW + timedelta(days=1))
    fake.holidays = b"<html><body>"
    caplog.set_level(logging.WARNING)
    report = run(fake, store, clean, now=NOW + timedelta(days=2))
    assert [error.split(" (")[0] for error in report.errors] == [f"{HOLIDAYS_URL}: not JSON"]
    assert "public-holidays: the response received at 2026-09-28 12:00:00+00:00 does not read" in (
        caplog.text
    )
    assert on(days(clean), date(2025, 11, 1)) == ("Toussaint", "Vacances de la Toussaint")


def test_a_damaged_older_response_is_left_aside(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    good = store.receipts("calendars")[0]
    fake.holidays = b"<html>"
    run(fake, store, clean, now=NOW + timedelta(days=1))
    (store.root / good.path).write_bytes(b"damaged")
    report = run(fake, store, clean, now=NOW + timedelta(days=2))
    assert [error.split(" (")[0] for error in report.errors] == [
        f"{HOLIDAYS_URL}: not JSON",
        "public-holidays: no readable response",
        f"raw layer: {good.path}: Not a gzipped file",
    ]


def test_without_a_readable_calendar_the_days_are_not_written(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    fake.school = b"not parquet"
    report = run(fake, store, clean)
    assert [error.split(" (")[0] for error in report.errors] == [
        f"{SCHOOL_URL}: not a Parquet export",
        "school-holidays: no readable response",
    ]
    assert not (clean / "calendars" / "days.parquet").exists()


def test_days_that_end_before_tomorrow_are_an_error(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    fake.holidays = holidays_json(range(2025, 2026))
    report = run(fake, store, clean)
    assert report.errors == ["calendars: the days end on 2025-12-31, before tomorrow, 2026-09-28"]
    # As for every source, the checks keep a file as it was only for invalid rows (ADR 022).
    assert days(clean)["day"].max() == date(2025, 12, 31)


def test_a_school_calendar_that_ends_within_a_year_is_a_warning(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    # Without the school year 2027-2028, the calendar ends with the summer of 2027.
    fake.school = school_export(school_rows([p for p in LYON if p[0] != "2027-2028"]))
    report = run(fake, store, clean)
    assert report.warnings == [
        "calendars: the school calendar of Lyon ends on 2027-09-01, less than a year ahead"
    ]


@pytest.mark.parametrize(
    "name",
    [
        "Vacances de la Toussaint",
        "Vacances de Noël",
        "Vacances d'Hiver",
        "Vacances de Printemps",
        "Vacances d'Été",
    ],
)
def test_a_school_year_without_its_usual_holidays_is_a_warning(
    fake: FakeCalendars, store: RawStore, clean: Path, name: str
) -> None:
    fake.school = school_export(school_rows([p for p in LYON if p[:2] != ("2025-2026", name)]))
    report = run(fake, store, clean)
    # The last school year, still partly published, is not checked.
    assert report.warnings == [f"calendars: the school year 2025-2026 of Lyon has no {name}"]


def test_a_school_year_missing_at_the_start_is_a_warning(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    # The summer of 2025 opens the history: its school year only matters for it.
    fake.school = school_export(school_rows([p for p in LYON if p[0] != "2024-2025"]))
    report = run(fake, store, clean)
    assert report.warnings == ["calendars: the school year 2024-2025 of Lyon has no Vacances d'Été"]


def test_no_day_since_the_start_of_the_history_is_an_error(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    fake.holidays = holidays_json(range(2024, 2025))
    report = run(fake, store, clean)
    assert "calendars: no day known since 2025-07-01" in report.errors


def test_an_error_of_a_source_stops_the_run(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        return (
            httpx2.Response(503, text="busy")
            if str(request.url) == SCHOOL_URL
            else fake.handle(request)
        )

    with (
        client(httpx2.MockTransport(refuse)) as http,
        pytest.raises(httpx2.HTTPStatusError, match="503"),
    ):
        ingest(http, store, clean, now=NOW, since=SINCE, sleep=lambda seconds: None)


def test_the_raw_layer_is_checked(fake: FakeCalendars, store: RawStore, clean: Path) -> None:
    run(fake, store, clean)
    (store.root / "calendars" / "stray.json.gz").write_bytes(b"x")
    report = run(fake, store, clean)
    assert report.errors == ["raw layer: calendars/stray.json.gz: not in the manifest"]


def test_the_run_is_logged(
    fake: FakeCalendars, store: RawStore, clean: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    run(fake, store, clean)
    assert caplog.text.count("new response kept in") == 4
    assert "calendars: 4 requests, 4 new responses" in caplog.text


def updates(clean: Path) -> dict[str, tuple[datetime, datetime]]:
    """The date each calendar was last updated, and the reception of the response that says so."""
    frame = pl.read_parquet(clean / "calendars" / "updates.parquet")
    return {
        row["dataset"]: (row["updated_at"], row["received_at"])
        for row in frame.iter_rows(named=True)
    }


def test_each_calendar_keeps_the_date_its_source_published(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    assert run(fake, store, clean) == Report()
    assert updates(clean) == {
        "public-holidays": (datetime(2026, 9, 27, 18, 33, 16, tzinfo=UTC), NOW),
        "school-holidays": (datetime(2026, 9, 18, 8, 35, 17, 505000, tzinfo=UTC), NOW),
    }


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("last week", "unexpected last_modified 'last week'"),
        ("2026-09-27T18:33:16", "unexpected last_modified '2026-09-27T18:33:16'"),
        (None, "unexpected last_modified None"),
        # A date after the run is faulty metadata, as Enedis's publications (ADR 026).
        ("2026-09-29T12:00:01+00:00", "unexpected last_modified '2026-09-29T12:00:01+00:00'"),
    ],
)
def test_a_faulty_date_is_an_error_and_the_date_kept_stays(
    fake: FakeCalendars, store: RawStore, clean: Path, value: object, message: str
) -> None:
    run(fake, store, clean)
    before = updates(clean)
    fake.holidays_updated = value
    fake.school_updated = "2026-09-25T10:00:00+00:00"
    later = NOW + timedelta(hours=1)
    report = run(fake, store, clean, now=later)
    assert [error.split(": ", 1)[1] for error in report.errors] == [message]
    kept = updates(clean)
    assert kept["public-holidays"] == before["public-holidays"]
    # The other date goes on, and so do the days.
    assert kept["school-holidays"] == (datetime(2026, 9, 25, 10, tzinfo=UTC), later)
    assert days(clean).height > 0


@pytest.mark.parametrize(
    ("url", "body", "message"),
    [
        (HOLIDAYS_UPDATES_URL, b'{"resources": []}', f"no resource {HOLIDAYS_RESOURCE}"),
        (HOLIDAYS_UPDATES_URL, b"[]", f"no resource {HOLIDAYS_RESOURCE}"),
        (SCHOOL_UPDATES_URL, b'{"results": []}', "no date for fr-en-calendrier-scolaire"),
        (SCHOOL_UPDATES_URL, b"not json", "not JSON"),
    ],
)
def test_a_date_response_of_another_shape_is_an_error(
    fake: FakeCalendars, store: RawStore, clean: Path, url: str, body: bytes, message: str
) -> None:
    fake.bodies[url] = body
    report = run(fake, store, clean)
    assert len(report.errors) == 1 and message in report.errors[0]
    assert len(updates(clean)) == 1


URL = "https://example.test/calendar"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<html>", "not JSON"),
        (b"[]", "not an object of public holidays"),
        (b'["2026-11-01"]', "not an object of public holidays"),
        (b"{}", "not an object of public holidays"),
        (b'{"2026-13-01": "Toussaint"}', "an unexpected public holiday"),
        (b'{"20261101": "Toussaint"}', "an unexpected public holiday"),
        (b'{"2026-11-01": ""}', "an unexpected public holiday"),
        (b'{"2026-11-01": 1}', "an unexpected public holiday"),
        # A year cut short between two whole ones.
        (
            json.dumps(
                {
                    **json.loads(holidays_json(range(2025, 2026))),
                    "2026-01-01": "1er janvier",
                    "2026-05-01": "1er mai",
                    **json.loads(holidays_json(range(2027, 2028))),
                }
            ).encode(),
            "2 public holidays in 2026, not 10 or 11",
        ),
    ],
    ids=lambda value: value.decode() if isinstance(value, bytes) else None,
)
def test_the_public_holidays_must_keep_their_shape(body: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=message) as error:
        parse_holidays(body, URL)
    assert str(error.value).startswith(f"{URL}: ")


def test_the_public_holidays_are_read_by_day() -> None:
    holidays = parse_holidays(holidays_json(range(2026, 2027)), URL)
    assert len(holidays) == 11
    assert holidays[date(2026, 5, 14)] == "Ascension"


def shifted(name: str, column: str, hours: int) -> list[dict[str, object]]:
    rows = school_rows()
    for row in rows:
        if row["location"] == "Lyon" and row["description"] == name:
            instant = row[column]
            assert isinstance(instant, datetime)
            row[column] = instant + timedelta(hours=hours)
    return rows


def naive_export() -> bytes:
    """An export whose instants have no time zone."""
    table = pl.read_parquet(io.BytesIO(school_export(school_rows())))
    buffer = io.BytesIO()
    table.with_columns(pl.col("start_date").dt.replace_time_zone(None)).write_parquet(buffer)
    return buffer.getvalue()


def lyon_with(**changes: object) -> list[dict[str, object]]:
    rows = school_rows()
    for row in rows:
        if row["location"] == "Lyon" and row["description"] == "Vacances de Noël":
            row.update(changes)
    return rows


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not parquet", "not a Parquet export"),
        (school_export(school_rows(), drop="location"), "no column location"),
        (school_export([row for row in school_rows() if row["location"] != "Lyon"]), "no school"),
        # Not at midnight in Paris, at the start or at the end.
        (
            school_export(shifted("Vacances de Noël", "start_date", 1)),
            "Vacances de Noël 2025-2026 does not start and end at midnight in Paris",
        ),
        (
            school_export(shifted("Vacances de Noël", "end_date", -1)),
            "Vacances de Noël 2025-2026 does not start and end at midnight in Paris",
        ),
        (
            school_export(lyon_with(end_date=midnight(date(2025, 12, 19)))),
            "Vacances de Noël 2025-2026 ends before it starts",
        ),
        (
            school_export(lyon_with(end_date=midnight(date(2026, 2, 8)))),
            "Vacances d'Hiver 2025-2026 overlaps Vacances de Noël",
        ),
        (school_export(lyon_with(annee_scolaire="2025")), "an unexpected school year, '2025'"),
        (
            school_export(lyon_with(annee_scolaire="2025-2027")),
            "an unexpected school year, '2025-2027'",
        ),
        (naive_export(), "no column start_date of the expected type"),
        (school_export(lyon_with(description=None)), "a period of Lyon without its"),
        (school_export(lyon_with(population=None)), "a period of Lyon without its population"),
        # A population the code does not know: its periods would vanish unseen.
        (school_export(lyon_with(population="Tous")), "an unexpected population of Lyon, 'Tous'"),
        (
            school_export(lyon_with(population="Élèves du premier degré")),
            "an unexpected population of Lyon, 'Élèves du premier degré'",
        ),
        # A whole school year missing between two others.
        (
            school_export(school_rows([p for p in LYON if p[0] != "2025-2026"])),
            "no school holidays of Lyon in 2025-2026",
        ),
        (
            school_export(lyon_with(end_date=midnight(date(2026, 6, 1)))),
            "Vacances de Noël 2025-2026 lasts 163 days, more than 75",
        ),
        # A date that stands for an end not known yet, out of the calendar of Python.
        (
            school_export(
                lyon_with(
                    start_date=datetime(9999, 12, 31, 23, tzinfo=UTC),
                    end_date=datetime(9999, 12, 31, 23, tzinfo=UTC),
                )
            ),
            "has a date out of range",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "export",
)
def test_the_school_calendar_must_keep_its_shape(content: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=re.escape(message)) as error:
        parse_school(content, URL)
    assert str(error.value).startswith(f"{URL}: ")


@pytest.mark.parametrize("name", ["scan_parquet", "read_parquet"])
def test_a_panic_of_polars_is_a_faulty_school_calendar(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # The footer and the rows: Polars may panic in each, and its panic is a BaseException.
    content = school_export(school_rows())

    def panic(*args: object, **options: object) -> pl.DataFrame:
        raise pl.exceptions.PanicException("index out of bounds")

    monkeypatch.setattr(pl, name, panic)
    with pytest.raises(SchemaError, match="not a Parquet export"):
        parse_school(content, URL)


def test_the_school_calendar_is_bounded_by_its_footer() -> None:
    # Parquet compresses by itself: a small file could unfold into millions of rows.
    rows = pl.DataFrame(school_rows())
    many = rows.sample(n=10_001, with_replacement=True, seed=1)
    with pytest.raises(SchemaError, match="10001 rows in 7 columns"):
        parse_school(school_export(many.to_dicts()), URL)
    wide = rows.with_columns(pl.lit("x").alias(f"extra_{i}") for i in range(14))
    buffer = io.BytesIO()
    wide.write_parquet(buffer)
    with pytest.raises(SchemaError, match=f"{rows.height} rows in 21 columns"):
        parse_school(buffer.getvalue(), URL)


def test_periods_that_follow_each_other_do_not_overlap() -> None:
    # Christmas ends the day before a period that would start on the day classes resume.
    rows = [
        *school_rows(),
        {
            "description": "Journée de la neige",
            "population": "-",
            "start_date": midnight(date(2026, 1, 5)),
            "end_date": midnight(date(2026, 1, 5)),
            "location": "Lyon",
            "zones": "Zone A",
            "annee_scolaire": "2025-2026",
        },
    ]
    periods = parse_school(school_export(rows), URL)
    snow = next(p for p in periods if p.name == "Journée de la neige")
    assert (snow.first, snow.last) == (date(2026, 1, 5), date(2026, 1, 5))


def calendar_days(last: date) -> pl.DataFrame:
    days = [SINCE + timedelta(days=i) for i in range((last - SINCE).days + 1)]
    return pl.DataFrame(
        {"day": days, "public_holiday": None, "school_holidays": None}
    ).with_columns(pl.col("public_holiday", "school_holidays").cast(pl.String))


# A school calendar long enough not to warn, and public holidays from 2025 to 2028.
FAR = [Period("Vacances d'Été", "2027-2028", date(2028, 7, 4), date(2028, 7, 4))]
HOLIDAYS = parse_holidays(holidays_json(range(2025, 2029)), "https://example.test/holidays")


@pytest.mark.parametrize(
    ("last", "failed"), [(date(2026, 9, 27), True), (date(2026, 9, 28), False)]
)
def test_the_days_must_reach_tomorrow(last: date, failed: bool) -> None:
    report = check(calendar_days(last), FAR, HOLIDAYS, since=SINCE, now=NOW)
    assert report.failed is failed


@pytest.mark.parametrize(("end", "warned"), [(date(2027, 9, 26), True), (date(2027, 9, 27), False)])
def test_a_school_calendar_must_reach_a_year_ahead(end: date, warned: bool) -> None:
    periods = [Period("Vacances d'Été", "2026-2027", date(2027, 7, 3), end)]
    report = check(calendar_days(date(2027, 1, 1)), periods, HOLIDAYS, since=SINCE, now=NOW)
    ahead = f"calendars: the school calendar of Lyon ends on {end}, less than a year ahead"
    assert (ahead in report.warnings) is warned


@pytest.mark.parametrize(
    ("years", "problem"),
    [
        # The public holidays must cover the start of the history…
        (range(2026, 2029), "error"),
        # …and reach a year ahead, as the school calendar.
        (range(2025, 2027), "warning"),
    ],
)
def test_the_public_holidays_must_cover_the_history_and_a_year_ahead(
    years: range, problem: str
) -> None:
    holidays = parse_holidays(holidays_json(years), URL)
    report = check(calendar_days(date(2026, 12, 31)), FAR, holidays, since=SINCE, now=NOW)
    if problem == "error":
        assert report.errors == ["calendars: the public holidays start in 2026, after 2025-07-01"]
    else:
        ahead = "calendars: the public holidays end on 2026-12-31, less than a year ahead"
        assert ahead in report.warnings


def test_the_school_holidays_are_those_of_the_pupils_of_lyon() -> None:
    periods = parse_school(school_export(school_rows()), URL)
    summer = next(p for p in periods if (p.year, p.name) == ("2025-2026", "Vacances d'Été"))
    assert (summer.first, summer.last) == (date(2026, 7, 4), date(2026, 8, 31))
    bridge = next(p for p in periods if (p.year, p.name) == ("2026-2027", "Pont de l'Ascension"))
    assert (bridge.first, bridge.last) == (date(2027, 5, 7), date(2027, 5, 7))
    assert len(periods) == len([p for p in LYON if p[2] != "Enseignants"])
