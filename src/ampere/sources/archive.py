"""Fetching a source: every response goes to the raw layer before anything reads it (ADR 022)."""

import logging
from collections.abc import Callable
from datetime import date

import httpx2

from ampere.data.http import get
from ampere.data.raw import RawStore, Receipt

log = logging.getLogger(__name__)

JSON = "application/json"
# The start of the history, the same for every source: the first day of the Enedis window.
SINCE = date(2023, 7, 1)
# Seconds between two requests to a source, out of politeness.
PAUSE = 0.5


def fetch(
    http: httpx2.Client,
    store: RawStore,
    *,
    source: str,
    dataset: str,
    url: str,
    content_type: str,
    extension: str,
    max_bytes: int,
    sleep: Callable[[float], None],
    request: str | None = None,
    fingerprint: Callable[[bytes], bytes] | None = None,
) -> tuple[bytes, Receipt]:
    """GET a response of the expected type, keep it in the raw layer, and return its body with
    the receipt that records it.

    The raw layer files it under its request, the URL unless another name is given, and compares
    it with the last response of that request, byte for byte or by its fingerprint.
    """
    fetched = get(http, url, content_type=content_type, max_bytes=max_bytes, sleep=sleep)
    saved = store.save(
        source=source,
        dataset=dataset,
        request=url if request is None else request,
        url=fetched.url,
        content_type=fetched.content_type,
        content=fetched.content,
        extension=extension,
        fingerprint=fingerprint,
    )
    if saved.new:
        log.info("%s: new response kept in %s", url, saved.receipt.path)
    return fetched.content, saved.receipt


def fetch_json(
    http: httpx2.Client,
    store: RawStore,
    *,
    source: str,
    dataset: str,
    url: str,
    max_bytes: int,
    sleep: Callable[[float], None],
    request: str | None = None,
    fingerprint: Callable[[bytes], bytes] | None = None,
) -> tuple[bytes, Receipt]:
    """fetch() for a JSON response."""
    return fetch(
        http,
        store,
        source=source,
        dataset=dataset,
        url=url,
        content_type=JSON,
        extension="json",
        max_bytes=max_bytes,
        sleep=sleep,
        request=request,
        fingerprint=fingerprint,
    )
