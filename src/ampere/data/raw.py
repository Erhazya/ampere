"""The raw layer: every response of a source, kept as received (ADR 022).

Each response is stored compressed in gzip, under raw/<source>/<dataset>/<year>/<month>/, and the
manifest of its source, raw/<source>/manifest.jsonl, gets one line per file. A response identical
to the last one received for the same request adds nothing: running an ingestion again leaves the
archive as it is. A source whose responses change at every call, while their data do not, gives a
fingerprint of what must match (ADR 025).

This layer is the only copy of what the sources sent. Each file reaches the disk (fsync) before the
manifest line that records it, so that even a power cut never leaves a line without its file.
"""

import fcntl
import gzip
import hashlib
import json
import logging
import os
import re
import tempfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

# Lowercase words joined by hyphens: a name can never lead out of the raw folder.
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Marks a folder as a raw layer. Without it, a wrong path or an unmounted volume would pass for an
# empty archive, and a new one would start there without a word.
MARKER = ".ampere-raw"
MANIFEST = "manifest.jsonl"
LOCK = ".lock"


class DamagedRawFile(ValueError):
    """A raw file that is missing, cut short, or not what its manifest line says."""


class ManifestError(ValueError):
    """A manifest that cannot be read, beyond an unfinished last line."""


@dataclass(frozen=True)
class Receipt:
    """One file of the raw layer, as the manifest of its source records it."""

    source: str
    dataset: str
    request: str  # what was asked for, such as the URL
    url: str  # where the response came from, after any redirect
    content_type: str
    path: str  # relative to the raw folder
    received_at: datetime  # in UTC, to the second, like the file name
    sha256: str  # of the response as received
    size: int  # of the response as received, in bytes

    def __post_init__(self) -> None:
        if self.received_at.utcoffset() != timedelta(0) or self.received_at.microsecond:
            raise ValueError("a reception time is kept in UTC, to the second")


@dataclass(frozen=True)
class Saved:
    """What save() did: the receipt of the response, and whether a file was added for it."""

    receipt: Receipt
    new: bool


@dataclass
class Manifest:
    """A manifest as read, with the size it had then, to notice what another run added since."""

    size: int = 0
    end: int = 0  # the size without an unfinished last line
    receipts: list[Receipt] = field(default_factory=list)
    last: dict[tuple[str, str], Receipt] = field(default_factory=dict)

    def add(self, receipt: Receipt) -> None:
        self.receipts.append(receipt)
        self.last[receipt.dataset, receipt.request] = receipt


def utc_now() -> datetime:
    return datetime.now(UTC)


class RawStore:
    """The raw folder of the data directory."""

    def __init__(self, root: Path, clock: Callable[[], datetime] = utc_now) -> None:
        """Open an existing raw layer. Its clock gives the reception time of a response: now,
        unless a test plays a run at another date."""
        if not (root / MARKER).is_file():
            raise FileNotFoundError(
                f"{root.absolute()} is not a raw layer, it has no {MARKER} file: check AMPERE_DATA "
                "and its volume, or, for a first use, create the layer once with `ampere data init`"
            )
        self.root = root
        self.clock = clock
        self.manifests: dict[str, Manifest] = {}

    @classmethod
    def create(cls, root: Path, clock: Callable[[], datetime] = utc_now) -> "RawStore":
        """Create a raw layer, or open it if it exists: a deployment step, never a daily one."""
        if not (root / MARKER).exists():
            write_atomically(root / MARKER, b"1\n")
        return cls(root, clock)

    def save(
        self,
        *,
        source: str,
        dataset: str,
        request: str,
        url: str,
        content_type: str,
        content: bytes,
        extension: str,
        received_at: datetime | None = None,
        fingerprint: Callable[[bytes], bytes] | None = None,
    ) -> Saved:
        """Keep a response, unless it is the same as the last one received for this request.

        The same means identical or, given a fingerprint, with the same fingerprint: the file kept
        then stays the first one received. The reception time comes from the clock when not
        given. A file lost or damaged since it was kept is written again from an identical
        response.
        """
        for name in (source, dataset, extension):
            check_name(name)
        received_at = reception_time(self.clock() if received_at is None else received_at)
        sha256 = hashlib.sha256(content).hexdigest()
        with self.locked(source):
            # An empty manifest first, for a new source: after a crash that stops the very first
            # save, the file left behind is only an orphan, not files without their manifest.
            if not self.manifest_path(source).exists():
                write_atomically(self.manifest_path(source), b"")
            manifest = self.load(source)
            last = manifest.last.get((dataset, request))
            if last is not None and last.sha256 == sha256:
                self.repair(last, content)
                return Saved(last, new=False)
            if last is not None and fingerprint and self.alike(last, content, fingerprint):
                return Saved(last, new=False)
            path = PurePosixPath(
                source,
                dataset,
                f"{received_at:%Y}",
                f"{received_at:%m}",
                f"{received_at:%Y%m%dT%H%M%SZ}-{sha256[:12]}.{extension}.gz",
            )
            # Without a time in the gzip header, the same content always gives the same file.
            write_atomically(self.root / path, gzip.compress(content, mtime=0))
            receipt = Receipt(
                source,
                dataset,
                request,
                url,
                content_type,
                path.as_posix(),
                received_at,
                sha256,
                len(content),
            )
            self.append(manifest, receipt)
            return Saved(receipt, new=True)

    def receipts(self, source: str) -> list[Receipt]:
        """Every file kept for a source, in the order they were kept."""
        return list(self.load(source).receipts)

    def last(self, source: str, dataset: str, request: str) -> Receipt | None:
        """The latest file kept for a request, if any."""
        return self.load(source).last.get((dataset, request))

    def latest(self, source: str, dataset: str) -> list[Receipt]:
        """The latest file kept for each request of a dataset, in the order the requests came."""
        return [receipt for (name, _), receipt in self.load(source).last.items() if name == dataset]

    def read(self, receipt: Receipt) -> bytes:
        """The response as received, after checking it against its SHA-256."""
        path = self.file(receipt)
        try:
            content = gzip.decompress(path.read_bytes())
        except (OSError, EOFError, zlib.error) as error:
            raise DamagedRawFile(f"{receipt.path}: {error}") from error
        if hashlib.sha256(content).hexdigest() != receipt.sha256:
            raise DamagedRawFile(f"{receipt.path} does not match its SHA-256 in the manifest")
        return content

    def verify(self, source: str) -> list[str]:
        """What is wrong between the manifest of a source and its files: nothing, if all is well."""
        problems = []
        receipts = self.receipts(source)
        # Two requests can share a file: each file is read once.
        for receipt in {receipt.path: receipt for receipt in receipts}.values():
            try:
                self.read(receipt)
            except DamagedRawFile as error:
                problems.append(str(error))
        recorded = {receipt.path for receipt in receipts}
        folder = self.root / source
        for file in sorted(folder.rglob("*")) if folder.exists() else []:
            relative = file.relative_to(self.root).as_posix()
            if not file.is_file() or file.name in (MANIFEST, LOCK):
                continue
            if file.name.endswith(".tmp"):
                problems.append(f"{relative}: left by an interrupted write")
            elif relative not in recorded:
                problems.append(f"{relative}: not in the manifest")
        return problems

    def file(self, receipt: Receipt) -> Path:
        path = PurePosixPath(receipt.path)
        if path.is_absolute() or ".." in path.parts:
            raise DamagedRawFile(f"{receipt.path} is outside the raw layer")
        return self.root / path

    def manifest_path(self, source: str) -> Path:
        check_name(source)
        return self.root / source / MANIFEST

    @contextmanager
    def locked(self, source: str) -> Iterator[None]:
        """One writer at a time for a source, across processes: the lock is a file of its folder."""
        folder = self.root / source
        make_folders(folder)
        with (folder / LOCK).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def load(self, source: str) -> Manifest:
        """The manifest of a source, read again only when its size has changed."""
        path = self.manifest_path(source)
        if not path.exists():
            folder = self.root / source
            if folder.exists() and any(folder.rglob("*.gz")):
                raise ManifestError(f"{path} is missing, but {folder} has files")
            return Manifest()
        size = path.stat().st_size
        cached = self.manifests.get(source)
        if cached is not None and cached.size == size:
            return cached
        data = path.read_bytes()
        # Everything after the last newline is a line an interrupted write never finished.
        end = data.rfind(b"\n") + 1
        if end < len(data):
            log.warning("%s ends with an unfinished line, which is ignored", path)
        manifest = Manifest(size=size, end=end)
        for number, line in enumerate(data[:end].splitlines(), start=1):
            try:
                manifest.add(from_line(json.loads(line)))
            except (ValueError, KeyError, TypeError) as error:
                raise ManifestError(f"{path}:{number}: {error}") from error
        self.manifests[source] = manifest
        return manifest

    def append(self, manifest: Manifest, receipt: Receipt) -> None:
        """Add a receipt to its manifest, once its file is on the disk."""
        path = self.manifest_path(receipt.source)
        if manifest.end < manifest.size:
            log.warning("%s: the unfinished last line is removed before a new one", path)
            os.truncate(path, manifest.end)
        with path.open("ab") as file:
            file.write((json.dumps(to_line(receipt)) + "\n").encode())
            file.flush()
            os.fsync(file.fileno())
        manifest.size = manifest.end = path.stat().st_size
        manifest.add(receipt)
        self.manifests[receipt.source] = manifest

    def alike(
        self, receipt: Receipt, content: bytes, fingerprint: Callable[[bytes], bytes]
    ) -> bool:
        """Whether a response has the fingerprint of a kept file. A damaged file vouches for
        nothing: the response is then kept beside it."""
        try:
            kept = self.read(receipt)
        except DamagedRawFile as error:
            log.warning("%s; the new response is kept beside it", error)
            return False
        return fingerprint(kept) == fingerprint(content)

    def repair(self, receipt: Receipt, content: bytes) -> None:
        """Write a lost or damaged file again, from an identical response."""
        try:
            self.read(receipt)
        except DamagedRawFile as error:
            log.warning("%s; written again from an identical response", error)
            write_atomically(self.file(receipt), gzip.compress(content, mtime=0))


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise ValueError(f"invalid name for the raw layer: {name!r}")


def reception_time(received_at: datetime) -> datetime:
    """UTC, to the second: the precision of the file names and of the manifest."""
    if received_at.utcoffset() is None:
        raise ValueError("the reception time needs a time zone")
    return received_at.astimezone(UTC).replace(microsecond=0)


def write_atomically(path: Path, data: bytes) -> None:
    """Write a file whole or not at all, even across a power cut.

    The data goes to a temporary file with a unique name and reaches the disk (fsync) before the
    rename; the folder then reaches the disk too, so that the new name is not lost either.
    """
    make_folders(path.parent)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as file:
            os.fchmod(file.fileno(), 0o644)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    sync_folder(path.parent)


def make_folders(folder: Path) -> None:
    """Create the missing folders of a path, each recorded on the disk in its parent."""
    missing = [path for path in (folder, *folder.parents) if not path.exists()]
    for new in reversed(missing):
        new.mkdir(exist_ok=True)
        sync_folder(new.parent)


def sync_folder(folder: Path) -> None:
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def to_line(receipt: Receipt) -> dict[str, Any]:
    return {
        "source": receipt.source,
        "dataset": receipt.dataset,
        "request": receipt.request,
        "url": receipt.url,
        "content_type": receipt.content_type,
        "path": receipt.path,
        "received_at": receipt.received_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": receipt.sha256,
        "size": receipt.size,
    }


def from_line(line: dict[str, Any]) -> Receipt:
    return Receipt(
        source=line["source"],
        dataset=line["dataset"],
        request=line["request"],
        url=line["url"],
        content_type=line["content_type"],
        path=line["path"],
        received_at=datetime.fromisoformat(line["received_at"]),
        sha256=line["sha256"],
        size=line["size"],
    )
