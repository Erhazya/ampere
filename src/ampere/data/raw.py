"""The raw layer: every response of a source, kept as received (ADR 022).

Each response is stored compressed in gzip, under raw/<source>/<dataset>/<year>/<month>/, and the
manifest of its source, raw/<source>/manifest.jsonl, gets one line per file. A response identical
to the last one received for the same request adds nothing: running an ingestion again leaves the
archive as it is.
"""

import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Lowercase words joined by hyphens: a name can never lead out of the raw folder.
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True)
class Receipt:
    """One file of the raw layer, as the manifest of its source records it."""

    source: str
    dataset: str
    request: str  # what was asked for, such as the URL
    path: str  # relative to the raw folder
    received_at: datetime  # in UTC
    sha256: str  # of the response as received
    size: int  # of the response as received, in bytes


class RawStore:
    """The raw folder of the data directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(
        self,
        source: str,
        dataset: str,
        request: str,
        content: bytes,
        received_at: datetime,
        extension: str,
    ) -> Receipt:
        """Keep a response, unless it is the same as the last one received for this request."""
        for name in (source, dataset, extension):
            check_name(name)
        if received_at.tzinfo is None:
            raise ValueError("the reception time needs a time zone")
        received_at = received_at.astimezone(UTC)
        sha256 = hashlib.sha256(content).hexdigest()
        last = self.last(source, dataset, request)
        if last is not None and last.sha256 == sha256:
            return last
        path = Path(
            source,
            dataset,
            f"{received_at:%Y}",
            f"{received_at:%m}",
            f"{received_at:%Y%m%dT%H%M%SZ}-{sha256[:12]}.{extension}.gz",
        )
        # Without a time in the gzip header, the same content always gives the same file.
        write_atomically(self.root / path, gzip.compress(content, mtime=0))
        receipt = Receipt(
            source, dataset, request, path.as_posix(), received_at, sha256, len(content)
        )
        with self.manifest(source).open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(to_line(receipt)) + "\n")
        return receipt

    def receipts(self, source: str) -> list[Receipt]:
        """Every file kept for a source, oldest first."""
        manifest = self.manifest(source)
        if not manifest.exists():
            return []
        lines = manifest.read_text(encoding="utf-8").splitlines()
        return [from_line(json.loads(line)) for line in lines]

    def last(self, source: str, dataset: str, request: str) -> Receipt | None:
        """The latest file kept for a request, if any."""
        matches = [
            receipt
            for receipt in self.receipts(source)
            if receipt.dataset == dataset and receipt.request == request
        ]
        return matches[-1] if matches else None

    def read(self, receipt: Receipt) -> bytes:
        """The response as received, after checking it against its SHA-256."""
        content = gzip.decompress((self.root / receipt.path).read_bytes())
        if hashlib.sha256(content).hexdigest() != receipt.sha256:
            raise ValueError(f"{receipt.path} does not match its SHA-256 in the manifest")
        return content

    def manifest(self, source: str) -> Path:
        check_name(source)
        return self.root / source / "manifest.jsonl"


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise ValueError(f"invalid name for the raw layer: {name!r}")


def write_atomically(path: Path, data: bytes) -> None:
    """Write under a temporary name, then rename: a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def to_line(receipt: Receipt) -> dict[str, Any]:
    return {
        "source": receipt.source,
        "dataset": receipt.dataset,
        "request": receipt.request,
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
        path=line["path"],
        received_at=datetime.fromisoformat(line["received_at"]),
        sha256=line["sha256"],
        size=line["size"],
    )
