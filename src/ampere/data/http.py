"""HTTP access to the sources (ADR 022): one client, patient with passing failures only.

get() returns a response only when it is what a source should send: a 200, from the host that was
asked, over HTTPS, of the expected type, not empty, within a size limit. Anything else raises, so
that nothing unexpected reaches the raw layer.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx2

from ampere import __version__

log = logging.getLogger(__name__)

# Lets a source know who asks, and where to reach the project.
USER_AGENT = f"ampere/{__version__} (+https://github.com/Erhazya/ampere)"
# For each step: connecting, then each wait for the next piece of the answer.
TIMEOUT = 30.0
# For a whole get(), its tries and waits included.
DEADLINE = 300.0
# The largest answer accepted, once decompressed.
MAX_BYTES = 200_000_000
# The waits, in seconds, before the second and the third try.
WAITS = (1.0, 2.0)
# The longest wait a Retry-After header can ask for.
MAX_RETRY_AFTER = 60.0
# Network failures that may pass: a timeout, a refused or lost connection, an answer cut short.
PASSING_ERRORS = (httpx2.TimeoutException, httpx2.NetworkError, httpx2.RemoteProtocolError)


class UnexpectedResponse(Exception):
    """An answer that is not what the source should send, kept out of the raw layer."""


@dataclass(frozen=True)
class Fetched:
    """A response as received: its body, the URL it came from after redirects, and its type."""

    content: bytes
    url: str
    content_type: str


def client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    """The client for every source; the tests give it a simulated transport."""
    return httpx2.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        transport=transport,
    )


def passing(status: int) -> bool:
    """Codes that say "not now": too many requests, or a server in trouble."""
    return status == 429 or status >= 500


def get(
    http: httpx2.Client,
    url: str,
    *,
    content_type: str,
    allow_empty: bool = False,
    max_bytes: int = MAX_BYTES,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Fetched:
    """A GET of a source. A passing failure gets two more tries, 1 s then 2 s later.

    A 429 or a 503 can ask for a longer wait with its Retry-After header, 60 s at most. The tries
    stop at the deadline, and the error of the last one goes up.
    """
    deadline = clock() + DEADLINE
    for attempt, wait in enumerate(WAITS, start=1):
        try:
            return fetch(http, url, content_type, allow_empty, max_bytes, deadline, clock)
        except PASSING_ERRORS as error:
            reason, pause = f"{type(error).__name__}: {error}", wait
        except httpx2.HTTPStatusError as error:
            if not passing(error.response.status_code):
                raise
            reason = f"HTTP {error.response.status_code}"
            pause = max(wait, retry_after(error.response))
        if clock() + pause > deadline:
            raise TimeoutError(f"{url}: still failing after {DEADLINE:.0f} s ({reason})")
        log.warning(
            "%s: try %d of %d failed (%s); next try in %.0f s",
            url,
            attempt,
            len(WAITS) + 1,
            reason,
            pause,
        )
        sleep(pause)
    # The last try: whatever happens now is final.
    return fetch(http, url, content_type, allow_empty, max_bytes, deadline, clock)


def fetch(
    http: httpx2.Client,
    url: str,
    content_type: str,
    allow_empty: bool,
    max_bytes: int,
    deadline: float,
    clock: Callable[[], float],
) -> Fetched:
    """One try: a whole response, checked, or an exception."""
    with http.stream("GET", url) as response:
        check_status(response)
        asked = httpx2.URL(url)
        if response.url.scheme != "https" or response.url.host != asked.host:
            raise UnexpectedResponse(f"{url} was redirected to {response.url}")
        received_type = response.headers.get("content-type", "")
        if received_type.split(";")[0].strip().lower() != content_type:
            raise UnexpectedResponse(
                f"{url} answered {received_type or 'without a content type'}, not {content_type}"
            )
        announced = int(response.headers.get("content-length", 0))
        if announced > max_bytes:
            raise UnexpectedResponse(
                f"{url} announces {announced} bytes, more than {max_bytes} bytes"
            )
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise UnexpectedResponse(f"{url} sent more than {max_bytes} bytes")
            if clock() > deadline:
                raise TimeoutError(f"{url}: no whole answer within {DEADLINE:.0f} s")
            chunks.append(chunk)
        final_url = str(response.url)
    content = b"".join(chunks)
    if not content and not allow_empty:
        raise UnexpectedResponse(f"{url} answered 200 with an empty body")
    return Fetched(content, final_url, received_type)


def check_status(response: httpx2.Response) -> None:
    """Let a 200 through. Another success is unexpected; an error raises HTTPStatusError, noted
    with the start of its body, which often says why."""
    status = response.status_code
    if status == 200:
        return
    if 200 <= status < 300:
        raise UnexpectedResponse(f"{response.url} answered {status}, not 200")
    try:
        response.raise_for_status()
    except httpx2.HTTPStatusError as error:
        excerpt = next(response.iter_bytes(), b"")[:500]
        error.add_note(f"start of the body: {excerpt!r}")
        raise


def retry_after(response: httpx2.Response) -> float:
    """The wait a Retry-After header asks for, in seconds, 60 at most; 0 without one.

    The header may also hold a date: that rare form falls back to the usual waits.
    """
    try:
        seconds = float(response.headers.get("retry-after", "0"))
    except ValueError:
        return 0.0
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)
