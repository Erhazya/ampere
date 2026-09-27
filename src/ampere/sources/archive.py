"""Fetching a source: every response goes to the raw layer before anything reads it (ADR 022)."""

import logging
from collections.abc import Callable

import httpx2

from ampere.data.http import get
from ampere.data.raw import RawStore, Receipt

log = logging.getLogger(__name__)

JSON = "application/json"


def fetch_json(
    http: httpx2.Client,
    store: RawStore,
    *,
    source: str,
    dataset: str,
    url: str,
    max_bytes: int,
    sleep: Callable[[float], None],
) -> tuple[bytes, Receipt]:
    """GET a JSON response, keep it in the raw layer, and return its body with the receipt
    that records it."""
    fetched = get(http, url, content_type=JSON, max_bytes=max_bytes, sleep=sleep)
    saved = store.save(
        source=source,
        dataset=dataset,
        request=url,
        url=fetched.url,
        content_type=fetched.content_type,
        content=fetched.content,
        extension="json",
    )
    if saved.new:
        log.info("%s: new response kept in %s", url, saved.receipt.path)
    return fetched.content, saved.receipt
