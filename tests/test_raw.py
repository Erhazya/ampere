"""The raw layer: responses kept as received, with a manifest per source."""

import gzip
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ampere.data.raw import MARKER, DamagedRawFile, ManifestError, RawStore, Saved

RECEIVED = datetime(2026, 9, 27, 12, 30, 5, tzinfo=UTC)
URL = "https://example.test/prices/2026-09-26"


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore.create(tmp_path / "raw")


def save(
    store: RawStore,
    content: bytes,
    received_at: datetime | None = RECEIVED,
    request: str = URL,
    dataset: str = "prices",
    fingerprint: Callable[[bytes], bytes] | None = None,
) -> Saved:
    return store.save(
        source="smard",
        dataset=dataset,
        request=request,
        url=request,
        content_type="application/json",
        content=content,
        extension="json",
        received_at=received_at,
        fingerprint=fingerprint,
    )


def without_took(content: bytes) -> bytes:
    """A fingerprint that leaves out "took", like the computing time some sources add."""
    body = json.loads(content)
    body.pop("took", None)
    return json.dumps(body, sort_keys=True).encode()


def manifest_text(store: RawStore) -> str:
    return (store.root / "smard" / "manifest.jsonl").read_text(encoding="utf-8")


def test_a_response_is_kept_as_received(store: RawStore) -> None:
    saved = save(store, b'{"price": 81.5}')
    assert saved.new
    file = store.root / saved.receipt.path
    assert file.parent == store.root / "smard" / "prices" / "2026" / "09"
    assert file.name.startswith("20260927T123005Z-") and file.name.endswith(".json.gz")
    assert gzip.decompress(file.read_bytes()) == b'{"price": 81.5}'
    assert store.read(saved.receipt) == b'{"price": 81.5}'


def test_latest_gives_the_last_file_of_each_request_of_a_dataset(store: RawStore) -> None:
    first = save(store, b"[1]", request="https://example.test/a")
    save(store, b"[2]", request="https://example.test/b")
    save(store, b"[3]", request="https://example.test/a", dataset="index")
    again = save(store, b"[4]", RECEIVED + timedelta(hours=1), request="https://example.test/a")
    latest = store.latest("smard", "prices")
    assert [receipt.request for receipt in latest] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert latest[0] == again.receipt != first.receipt


def test_the_manifest_records_each_file(store: RawStore) -> None:
    receipt = save(store, b"[1, 2]").receipt
    lines = manifest_text(store).splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "source": "smard",
        "dataset": "prices",
        "request": URL,
        "url": URL,
        "content_type": "application/json",
        "path": receipt.path,
        "received_at": "2026-09-27T12:30:05Z",
        "sha256": hashlib.sha256(b"[1, 2]").hexdigest(),
        "size": 6,
    }
    assert store.receipts("smard") == [receipt]


def test_an_identical_response_adds_nothing(store: RawStore) -> None:
    first = save(store, b"[1, 2]").receipt
    again = save(store, b"[1, 2]", received_at=RECEIVED + timedelta(days=1))
    assert not again.new
    assert again.receipt == first
    assert len(store.receipts("smard")) == 1
    assert len(list(store.root.rglob("*.gz"))) == 1


def test_a_changed_response_adds_a_file(store: RawStore) -> None:
    save(store, b"[1, 2]")
    revised = save(store, b"[1, 3]", received_at=RECEIVED + timedelta(days=1))
    assert revised.new
    assert store.last("smard", "prices", URL) == revised.receipt
    assert len(store.receipts("smard")) == 2


def test_a_response_that_comes_back_is_kept_again(store: RawStore) -> None:
    # What was known at a given time must stay replayable: A, then B, then A again is three files.
    save(store, b"[1, 2]")
    save(store, b"[1, 3]", received_at=RECEIVED + timedelta(days=1))
    back = save(store, b"[1, 2]", received_at=RECEIVED + timedelta(days=2))
    assert back.new
    assert store.last("smard", "prices", URL) == back.receipt
    assert len(store.receipts("smard")) == 3


def test_a_response_with_the_same_fingerprint_adds_nothing(store: RawStore) -> None:
    first = save(store, b'{"price": 81.5, "took": 0.2}', fingerprint=without_took).receipt
    later = RECEIVED + timedelta(days=1)
    again = save(store, b'{"took": 0.9, "price": 81.5}', later, fingerprint=without_took)
    assert not again.new
    assert again.receipt == first
    assert store.read(first) == b'{"price": 81.5, "took": 0.2}'
    assert len(list(store.root.rglob("*.gz"))) == 1


def test_a_response_with_another_fingerprint_adds_a_file(store: RawStore) -> None:
    save(store, b'{"price": 81.5, "took": 0.2}', fingerprint=without_took)
    later = RECEIVED + timedelta(days=1)
    revised = save(store, b'{"price": 82.0, "took": 0.2}', later, fingerprint=without_took)
    assert revised.new
    assert store.last("smard", "prices", URL) == revised.receipt


def test_a_fingerprint_is_compared_with_the_last_response_only(store: RawStore) -> None:
    # A, then B, then A with another computing time: three files, as without a fingerprint.
    save(store, b'{"price": 1, "took": 0.1}', fingerprint=without_took)
    save(
        store, b'{"price": 2, "took": 0.1}', RECEIVED + timedelta(days=1), fingerprint=without_took
    )
    back = save(
        store, b'{"price": 1, "took": 0.5}', RECEIVED + timedelta(days=2), fingerprint=without_took
    )
    assert back.new
    assert len(store.receipts("smard")) == 3


@pytest.mark.parametrize("damage", ["delete", "truncate"])
def test_a_damaged_last_file_vouches_for_no_fingerprint(
    store: RawStore, damage: str, caplog: pytest.LogCaptureFixture
) -> None:
    receipt = save(store, b'{"price": 81.5, "took": 0.2}', fingerprint=without_took).receipt
    file = store.root / receipt.path
    if damage == "delete":
        file.unlink()
    else:
        file.write_bytes(file.read_bytes()[:10])
    later = RECEIVED + timedelta(days=1)
    again = save(store, b'{"price": 81.5, "took": 0.9}', later, fingerprint=without_took)
    assert again.new
    assert store.read(again.receipt) == b'{"price": 81.5, "took": 0.9}'
    assert f"{receipt.path}" in caplog.text
    assert "kept beside it" in caplog.text


def test_an_identical_response_still_repairs_its_file_with_a_fingerprint(
    store: RawStore, caplog: pytest.LogCaptureFixture
) -> None:
    receipt = save(store, b'{"price": 81.5, "took": 0.2}', fingerprint=without_took).receipt
    (store.root / receipt.path).unlink()
    later = RECEIVED + timedelta(days=1)
    again = save(store, b'{"price": 81.5, "took": 0.2}', later, fingerprint=without_took)
    assert not again.new
    assert store.read(receipt) == b'{"price": 81.5, "took": 0.2}'
    # The bytes are compared first: the log tells of a repair, not of a response kept beside.
    assert "written again" in caplog.text
    assert "kept beside it" not in caplog.text


def test_each_request_has_its_own_history(store: RawStore) -> None:
    save(store, b"[1, 2]")
    other = save(store, b"[1, 2]", request="https://example.test/prices/2026-09-27")
    assert other.new
    assert store.last("smard", "prices", "https://example.test/prices/2026-09-27") == other.receipt


def test_each_dataset_has_its_own_history(store: RawStore) -> None:
    save(store, b"[]")
    volumes = save(store, b"[]", dataset="volumes")
    assert volumes.new
    assert volumes.receipt.dataset == "volumes"
    assert len(store.receipts("smard")) == 2


def test_the_same_content_gives_the_same_file(tmp_path: Path) -> None:
    one, two = RawStore.create(tmp_path / "one"), RawStore.create(tmp_path / "two")
    a, b = save(one, b"same").receipt, save(two, b"same").receipt
    written = (one.root / a.path).read_bytes()
    assert written == (two.root / b.path).read_bytes()
    # Bytes 4 to 7 of a gzip header hold a time: zero here, so the file never depends on it.
    assert written[4:8] == bytes(4)


def test_a_receipt_reads_back_as_it_was_returned(store: RawStore) -> None:
    # The file names and the manifest keep whole seconds: so does the returned receipt.
    receipt = save(store, b"x", received_at=RECEIVED.replace(microsecond=123456)).receipt
    assert receipt.received_at == RECEIVED
    assert store.receipts("smard") == [receipt]


def test_the_reception_time_is_kept_in_utc(store: RawStore) -> None:
    paris = datetime(2026, 9, 27, 14, 30, 5, tzinfo=ZoneInfo("Europe/Paris"))
    receipt = save(store, b"x", received_at=paris).receipt
    # Two aware times of the same instant are equal whatever their zones: check the zone too.
    assert receipt.received_at == RECEIVED
    assert receipt.received_at.utcoffset() == timedelta(0)
    assert Path(receipt.path).name.startswith("20260927T123005Z-")


def test_the_reception_time_defaults_to_now(store: RawStore) -> None:
    before = datetime.now(UTC).replace(microsecond=0)
    receipt = save(store, b"x", received_at=None).receipt
    assert before <= receipt.received_at <= datetime.now(UTC)


def test_the_reception_time_comes_from_the_clock_of_the_layer(tmp_path: Path) -> None:
    # A test that plays a run at another date gives its own clock.
    moment = datetime(2026, 6, 20, 15, 0, 5, 700_000, tzinfo=ZoneInfo("Europe/Paris"))
    store = RawStore.create(tmp_path / "raw", clock=lambda: moment)
    assert save(store, b"x", received_at=None).receipt.received_at == datetime(
        2026, 6, 20, 13, 0, 5, tzinfo=UTC
    )
    later = RawStore(store.root, clock=lambda: moment + timedelta(hours=1))
    assert save(later, b"y", received_at=None).receipt.received_at == datetime(
        2026, 6, 20, 14, 0, 5, tzinfo=UTC
    )


def test_a_reception_time_without_a_time_zone_is_refused(store: RawStore) -> None:
    with pytest.raises(ValueError, match="time zone"):
        save(store, b"x", received_at=datetime(2026, 9, 27, 12, 30))


def test_a_receipt_is_in_utc_to_the_second(store: RawStore) -> None:
    receipt = save(store, b"x").receipt
    with pytest.raises(ValueError, match="UTC"):
        replace(receipt, received_at=RECEIVED.replace(microsecond=1))
    with pytest.raises(ValueError, match="UTC"):
        replace(receipt, received_at=RECEIVED.astimezone(ZoneInfo("Europe/Paris")))


@pytest.mark.parametrize("name", ["../other", "Prices", "", "a/b", "json\n"])
@pytest.mark.parametrize("argument", ["source", "dataset", "extension"])
def test_names_cannot_leave_the_raw_folder(store: RawStore, argument: str, name: str) -> None:
    names = {"source": "smard", "dataset": "prices", "extension": "json"} | {argument: name}
    with pytest.raises(ValueError, match="invalid"):
        store.save(
            source=names["source"],
            dataset=names["dataset"],
            request=URL,
            url=URL,
            content_type="application/json",
            content=b"x",
            extension=names["extension"],
            received_at=RECEIVED,
        )


def test_a_source_never_asked_has_no_receipts(store: RawStore) -> None:
    assert store.receipts("smard") == []
    assert store.last("smard", "prices", URL) is None


def test_a_folder_that_is_not_a_raw_layer_is_refused(tmp_path: Path) -> None:
    # A wrong path or an unmounted volume must not pass for an empty archive.
    with pytest.raises(FileNotFoundError, match=MARKER):
        RawStore(tmp_path / "elsewhere")
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match=MARKER):
        RawStore(tmp_path / "empty")


def test_creating_a_raw_layer_again_keeps_it(store: RawStore) -> None:
    receipt = save(store, b"x").receipt
    assert RawStore.create(store.root).receipts("smard") == [receipt]
    assert RawStore(store.root).receipts("smard") == [receipt]


@pytest.mark.parametrize("damage", ["delete", "truncate"])
def test_a_lost_file_is_written_again_from_an_identical_response(
    store: RawStore, damage: str, caplog: pytest.LogCaptureFixture
) -> None:
    receipt = save(store, b"[1, 2]").receipt
    file = store.root / receipt.path
    if damage == "delete":
        file.unlink()
    else:
        file.write_bytes(file.read_bytes()[:10])
    again = save(store, b"[1, 2]", received_at=RECEIVED + timedelta(days=1))
    assert not again.new
    assert store.read(receipt) == b"[1, 2]"
    assert "written again" in caplog.text


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("delete", "No such file"),
        ("truncate", "ended before"),
        ("not gzip", "Not a gzipped file"),
        ("other content", "does not match its SHA-256"),
    ],
)
def test_reading_a_damaged_file_fails_with_its_path(
    store: RawStore, damage: str, reason: str
) -> None:
    receipt = save(store, b"[1, 2]").receipt
    file = store.root / receipt.path
    match damage:
        case "delete":
            file.unlink()
        case "truncate":
            file.write_bytes(file.read_bytes()[:10])
        case "not gzip":
            file.write_bytes(b"not gzip at all")
        case "other content":
            file.write_bytes(gzip.compress(b"[9, 9]"))
    with pytest.raises(DamagedRawFile, match=reason) as error:
        store.read(receipt)
    assert receipt.path in str(error.value)


@pytest.mark.parametrize("path", ["/etc/passwd", "../../elsewhere.json.gz"])
def test_a_path_outside_the_raw_layer_is_refused(store: RawStore, path: str) -> None:
    receipt = save(store, b"x").receipt
    outside = replace(receipt, path=path)
    with pytest.raises(DamagedRawFile, match="outside"):
        store.read(outside)


def test_an_unfinished_last_line_is_ignored_then_replaced(
    store: RawStore, caplog: pytest.LogCaptureFixture
) -> None:
    first = save(store, b"[1, 2]").receipt
    # What an interrupted write leaves: the start of a line, without its newline.
    with (store.root / "smard" / "manifest.jsonl").open("a", encoding="utf-8") as manifest:
        manifest.write('{"source": "sma')
    assert store.receipts("smard") == [first]
    assert "unfinished" in caplog.text
    second = save(store, b"[1, 3]", received_at=RECEIVED + timedelta(days=1)).receipt
    # Read again by another store, as the next run would, rather than from this store's memory.
    assert RawStore(store.root).receipts("smard") == [first, second]
    assert len(manifest_text(store).splitlines()) == 2


def test_a_damaged_line_before_the_last_one_is_an_error(store: RawStore) -> None:
    save(store, b"[1, 2]")
    manifest = store.root / "smard" / "manifest.jsonl"
    manifest.write_text("not json\n" + manifest.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ManifestError, match=r"manifest\.jsonl:1"):
        store.receipts("smard")


def test_a_missing_manifest_next_to_its_files_is_an_error(store: RawStore) -> None:
    save(store, b"[1, 2]")
    (store.root / "smard" / "manifest.jsonl").unlink()
    with pytest.raises(ManifestError, match="missing"):
        store.receipts("smard")


def test_the_manifest_follows_what_another_run_wrote(tmp_path: Path) -> None:
    # Two runs on the same folder, like the daily job and a manual catch-up.
    one = RawStore.create(tmp_path / "raw")
    two = RawStore(tmp_path / "raw")
    save(one, b"[1, 2]")
    from_two = save(two, b"[1, 3]", received_at=RECEIVED + timedelta(days=1)).receipt
    assert one.last("smard", "prices", URL) == from_two
    assert not save(one, b"[1, 3]", received_at=RECEIVED + timedelta(days=2)).new


def test_verify_finds_nothing_in_a_sound_source(store: RawStore) -> None:
    save(store, b"[1, 2]")
    save(store, b"[1, 3]", received_at=RECEIVED + timedelta(days=1))
    assert store.verify("smard") == []


def test_verify_reports_missing_damaged_and_unrecorded_files(store: RawStore) -> None:
    missing = save(store, b"[1, 2]").receipt
    damaged = save(store, b"[1, 3]", received_at=RECEIVED + timedelta(days=1)).receipt
    (store.root / missing.path).unlink()
    (store.root / damaged.path).write_bytes(b"garbage")
    folder = store.root / "smard" / "prices" / "2026" / "09"
    (folder / "20260930T000000Z-abcdef012345.json.gz").write_bytes(gzip.compress(b"orphan"))
    (folder / ".left-by-a-crash.tmp").write_bytes(b"half")
    problems = "\n".join(store.verify("smard"))
    assert missing.path in problems and damaged.path in problems
    assert "20260930T000000Z-abcdef012345.json.gz" in problems
    assert "smard/prices/2026/09/.left-by-a-crash.tmp: left by an interrupted write" in problems
    assert len(store.verify("smard")) == 4


def test_an_empty_manifest_is_a_source_without_receipts(store: RawStore) -> None:
    # What a crash during the very first save of a source leaves: its manifest, still empty.
    (store.root / "smard").mkdir()
    (store.root / "smard" / "manifest.jsonl").write_bytes(b"")
    (store.root / "smard" / "orphan.json.gz").write_bytes(gzip.compress(b"x"))
    assert store.receipts("smard") == []
    assert store.verify("smard") == ["smard/orphan.json.gz: not in the manifest"]


def test_each_file_reaches_the_disk_before_its_manifest_line(
    store: RawStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A power cut must never leave a manifest line without its file: record what gets synced.
    synced: list[str] = []
    real_fsync = os.fsync

    def fsync(descriptor: int) -> None:
        synced.append(os.readlink(f"/proc/self/fd/{descriptor}"))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    receipt = save(store, b"[1, 2]").receipt
    file = store.root / receipt.path
    data = next(i for i, path in enumerate(synced) if path.endswith(".tmp"))
    folder = synced.index(str(file.parent))
    manifest = synced.index(str(store.root / "smard" / "manifest.jsonl"))
    assert data < folder < manifest
    assert synced[-1] == str(store.root / "smard" / "manifest.jsonl")
