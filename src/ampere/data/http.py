"""HTTP access to the sources (ADR 022): one client, patient with passing failures only."""

import time
from collections.abc import Callable

import httpx2

from ampere import __version__

# Lets a source know who asks, and where to reach the project.
USER_AGENT = f"ampere/{__version__} (+https://github.com/Erhazya/ampere)"
TIMEOUT = 30.0
# The waits, in seconds, before the second and the third try.
WAITS = (1.0, 2.0)
# Codes that say "not now": too many requests, or a server in trouble.
PASSING = {429, 500, 502, 503, 504}


def client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    """The client for every source; the tests give it a simulated transport."""
    return httpx2.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        transport=transport,
    )


def get(http: httpx2.Client, url: str, sleep: Callable[[float], None] = time.sleep) -> bytes:
    """The body of a GET. A network error, a 429 or a 5xx code gets two more tries."""
    for wait in WAITS:
        try:
            response = http.get(url)
        except httpx2.TransportError:
            sleep(wait)
            continue
        if response.status_code not in PASSING:
            return checked(response)
        sleep(wait)
    # The last try: whatever happens now is final.
    return checked(http.get(url))


def checked(response: httpx2.Response) -> bytes:
    """The body of a successful response; any other status raises HTTPStatusError."""
    response.raise_for_status()
    return response.content
