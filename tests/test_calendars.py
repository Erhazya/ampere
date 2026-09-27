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
    HOLIDAYS_URL,
    SCHEMA,
    SCHOOL_URL,
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
        self.requests: list[str] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
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
    assert fake.requests == [HOLIDAYS_URL, SCHOOL_URL]
    assert pauses == [0.5]
    receipts = store.receipts("calendars")
    assert [(r.dataset, r.request) for r in receipts] == [
        ("public-holidays", "metropole"),
        ("school-holidays", "fr-en-calendrier-scolaire"),
    ]
    assert receipts[0].path.endswith(".json.gz")
    assert receipts[1].path.endswith(".parquet.gz")


def test_the_same_calendars_add_nothing_to_the_raw_layer(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    run(fake, store, clean)
    fake.requests.clear()
    assert run(fake, store, clean, now=NOW + timedelta(days=1)) == Report()
    assert len(fake.requests) == 2
    assert len(store.receipts("calendars")) == 2


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


def test_a_school_calendar_that_ends_within_a_year_is_a_warning(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    # Without the school year 2027-2028, the calendar ends with the summer of 2027.
    fake.school = school_export(school_rows([p for p in LYON if p[0] != "2027-2028"]))
    report = run(fake, store, clean)
    assert report.warnings == [
        "calendars: the school calendar of Lyon ends on 2027-09-01, less than a year ahead"
    ]


def test_a_school_year_without_its_usual_holidays_is_a_warning(
    fake: FakeCalendars, store: RawStore, clean: Path
) -> None:
    fake.school = school_export(
        school_rows([p for p in LYON if p[:2] != ("2025-2026", "Vacances de Noël")])
    )
    report = run(fake, store, clean)
    # The last school year, still partly published, is not checked.
    assert report.warnings == [
        "calendars: the school year 2025-2026 of Lyon has no Vacances de Noël"
    ]


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
    assert caplog.text.count("new response kept in") == 2
    assert "calendars: 2 requests, 2 new responses" in caplog.text


URL = "https://example.test/calendar"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<html>", "not JSON"),
        (b"[]", "not an object of public holidays"),
        (b"{}", "not an object of public holidays"),
        (b'{"2026-13-01": "Toussaint"}', "an unexpected public holiday"),
        (b'{"20261101": "Toussaint"}', "an unexpected public holiday"),
        (b'{"2026-11-01": ""}', "an unexpected public holiday"),
        (b'{"2026-11-01": 1}', "an unexpected public holiday"),
    ],
)
def test_the_public_holidays_must_keep_their_shape(body: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=message) as error:
        parse_holidays(body, URL)
    assert str(error.value).startswith(f"{URL}: ")


def test_the_public_holidays_are_read_by_day() -> None:
    holidays = parse_holidays(holidays_json(range(2026, 2027)), URL)
    assert len(holidays) == 11
    assert holidays[date(2026, 5, 14)] == "Ascension"


def shifted(name: str, hours: int) -> list[dict[str, object]]:
    rows = school_rows()
    for row in rows:
        if row["location"] == "Lyon" and row["description"] == name:
            assert isinstance(row["start_date"], datetime)
            row["start_date"] = row["start_date"] + timedelta(hours=hours)
    return rows


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
        # Midnight in UTC, not in Paris.
        (school_export(shifted("Vacances de Noël", 1)), "does not start and end at midnight"),
        (
            school_export(lyon_with(end_date=midnight(date(2025, 12, 19)))),
            "Vacances de Noël 2025-2026 ends before it starts",
        ),
        (
            school_export(lyon_with(end_date=midnight(date(2026, 2, 8)))),
            "Vacances d'Hiver 2025-2026 overlaps Vacances de Noël",
        ),
        (school_export(lyon_with(annee_scolaire="2025")), "an unexpected school year, '2025'"),
        (school_export(lyon_with(description=None)), "a period of Lyon without its"),
    ],
)
def test_the_school_calendar_must_keep_its_shape(content: bytes, message: str) -> None:
    with pytest.raises(SchemaError, match=re.escape(message)) as error:
        parse_school(content, URL)
    assert str(error.value).startswith(f"{URL}: ")


def test_the_school_calendar_is_bounded_by_its_footer() -> None:
    # Parquet compresses by itself: a small file could unfold into millions of rows.
    rows = pl.DataFrame(school_rows())
    many = rows.sample(n=10_001, with_replacement=True, seed=1)
    with pytest.raises(SchemaError, match="10001 rows in 7 columns"):
        parse_school(school_export(many.to_dicts()), URL)


def test_the_school_holidays_are_those_of_the_pupils_of_lyon() -> None:
    periods = parse_school(school_export(school_rows()), URL)
    summer = next(p for p in periods if (p.year, p.name) == ("2025-2026", "Vacances d'Été"))
    assert (summer.first, summer.last) == (date(2026, 7, 4), date(2026, 8, 31))
    bridge = next(p for p in periods if (p.year, p.name) == ("2026-2027", "Pont de l'Ascension"))
    assert (bridge.first, bridge.last) == (date(2027, 5, 7), date(2027, 5, 7))
    assert len(periods) == len([p for p in LYON if p[2] != "Enseignants"])
