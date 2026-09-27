"""HTTP access to the sources, with responses simulated by httpx2's mock transport."""

from collections.abc import Callable

import httpx2
import pytest

from ampere.data.http import TIMEOUT, USER_AGENT, client, get

URL = "https://example.test/data.json"
Outcome = httpx2.Response | Exception


def fake(outcomes: list[Outcome]) -> tuple[httpx2.Client, list[httpx2.Request]]:
    """A client whose requests get the given outcomes, in order, and are recorded."""
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return client(httpx2.MockTransport(handle)), requests


@pytest.fixture
def waits() -> list[float]:
    return []


@pytest.fixture
def sleep(waits: list[float]) -> Callable[[float], None]:
    """Records the waits instead of sleeping."""
    return waits.append


def test_a_response_is_returned_as_received(sleep: Callable[[float], None]) -> None:
    http, requests = fake([httpx2.Response(200, content=b'{"a": 1}')])
    assert get(http, URL, sleep) == b'{"a": 1}'
    assert len(requests) == 1


def test_the_client_names_the_project_and_waits_30_seconds_at_most() -> None:
    http, requests = fake([httpx2.Response(200)])
    get(http, URL)
    assert requests[0].headers["user-agent"] == USER_AGENT
    assert "github.com/Erhazya/ampere" in USER_AGENT
    assert TIMEOUT == 30
    assert http.timeout.read == 30


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.Response(503),
        httpx2.Response(429),
        httpx2.ConnectError("connection refused"),
        httpx2.ReadTimeout("no answer"),
    ],
    ids=["503", "429", "connection refused", "timeout"],
)
def test_a_passing_failure_is_tried_again(
    failure: Outcome, sleep: Callable[[float], None], waits: list[float]
) -> None:
    http, requests = fake([failure, httpx2.Response(200, content=b"ok")])
    assert get(http, URL, sleep) == b"ok"
    assert len(requests) == 2
    assert waits == [1]


def test_after_three_failures_the_error_goes_up(
    sleep: Callable[[float], None], waits: list[float]
) -> None:
    http, requests = fake([httpx2.Response(503), httpx2.Response(503), httpx2.Response(503)])
    with pytest.raises(httpx2.HTTPStatusError):
        get(http, URL, sleep)
    assert len(requests) == 3
    assert waits == [1, 2]


def test_a_network_error_on_the_last_try_goes_up(sleep: Callable[[float], None]) -> None:
    http, _ = fake([httpx2.ConnectError("a"), httpx2.ConnectError("b"), httpx2.ConnectError("c")])
    with pytest.raises(httpx2.ConnectError):
        get(http, URL, sleep)


@pytest.mark.parametrize("status", [400, 403, 404])
def test_other_errors_stop_at_once(
    status: int, sleep: Callable[[float], None], waits: list[float]
) -> None:
    http, requests = fake([httpx2.Response(status)])
    with pytest.raises(httpx2.HTTPStatusError):
        get(http, URL, sleep)
    assert len(requests) == 1
    assert waits == []
