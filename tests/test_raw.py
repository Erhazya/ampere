"""The raw layer: responses kept as received, with a manifest."""

import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ampere.data.raw import RawStore, Receipt

RECEIVED = datetime(2026, 9, 27, 12, 30, 5, tzinfo=UTC)
URL = "https://example.test/prices/2026-09-26"


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


def save(
    store: RawStore, content: bytes, received_at: datetime = RECEIVED, request: str = URL
) -> Receipt:
    return store.save("smard", "prices", request, content, received_at, "json")


def test_a_response_is_kept_as_received(store: RawStore) -> None:
    receipt = save(store, b'{"price": 81.5}')
    file = store.root / receipt.path
    assert file.parent == store.root / "smard" / "prices" / "2026" / "09"
    assert file.name.startswith("20260927T123005Z-") and file.name.endswith(".json.gz")
    assert gzip.decompress(file.read_bytes()) == b'{"price": 81.5}'
    assert store.read(receipt) == b'{"price": 81.5}'


def test_the_manifest_records_each_file(store: RawStore) -> None:
    receipt = save(store, b"[1, 2]")
    lines = (store.root / "smard" / "manifest.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "source": "smard",
        "dataset": "prices",
        "request": URL,
        "path": receipt.path,
        "received_at": "2026-09-27T12:30:05Z",
        "sha256": hashlib.sha256(b"[1, 2]").hexdigest(),
        "size": 6,
    }
    assert store.receipts("smard") == [receipt]


def test_an_identical_response_adds_nothing(store: RawStore) -> None:
    first = save(store, b"[1, 2]")
    again = save(store, b"[1, 2]", received_at=datetime(2026, 9, 28, 12, tzinfo=UTC))
    assert again == first
    assert len(store.receipts("smard")) == 1
    assert len(list(store.root.rglob("*.gz"))) == 1


def test_a_changed_response_adds_a_file(store: RawStore) -> None:
    save(store, b"[1, 2]")
    revised = save(store, b"[1, 3]", received_at=datetime(2026, 9, 28, 12, tzinfo=UTC))
    assert len(store.receipts("smard")) == 2
    assert store.last("smard", "prices", URL) == revised
    assert len(list(store.root.rglob("*.gz"))) == 2


def test_each_request_has_its_own_history(store: RawStore) -> None:
    save(store, b"[1, 2]")
    other = save(store, b"[1, 2]", request="https://example.test/prices/2026-09-27")
    assert store.last("smard", "prices", "https://example.test/prices/2026-09-27") == other
    assert len(store.receipts("smard")) == 2


def test_the_same_content_gives_the_same_file(tmp_path: Path) -> None:
    one, two = RawStore(tmp_path / "one"), RawStore(tmp_path / "two")
    a, b = save(one, b"same"), save(two, b"same")
    written = (one.root / a.path).read_bytes()
    assert written == (two.root / b.path).read_bytes()
    # Bytes 4 to 7 of a gzip header hold a time: zero here, so the file never depends on it.
    assert written[4:8] == bytes(4)


def test_reading_a_damaged_file_fails(store: RawStore) -> None:
    receipt = save(store, b"[1, 2]")
    (store.root / receipt.path).write_bytes(gzip.compress(b"[9, 9]"))
    with pytest.raises(ValueError, match="does not match"):
        store.read(receipt)


def test_the_reception_time_is_kept_in_utc(store: RawStore) -> None:
    paris = datetime(2026, 9, 27, 14, 30, 5, tzinfo=ZoneInfo("Europe/Paris"))
    receipt = save(store, b"x", received_at=paris)
    # Two aware times of the same instant are equal whatever their zones: check the zone too.
    assert receipt.received_at == RECEIVED
    assert receipt.received_at.utcoffset() == timedelta(0)
    assert Path(receipt.path).name.startswith("20260927T123005Z-")


def test_a_reception_time_without_a_time_zone_is_refused(store: RawStore) -> None:
    with pytest.raises(ValueError, match="time zone"):
        save(store, b"x", received_at=datetime(2026, 9, 27, 12, 30))


@pytest.mark.parametrize("name", ["../other", "Prices", "", "a/b"])
def test_names_cannot_leave_the_raw_folder(store: RawStore, name: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        store.save(name, "prices", URL, b"x", RECEIVED, "json")
    with pytest.raises(ValueError, match="invalid"):
        store.save("smard", name, URL, b"x", RECEIVED, "json")


def test_a_store_without_files_has_no_receipts(store: RawStore) -> None:
    assert store.receipts("smard") == []
    assert store.last("smard", "prices", URL) is None
