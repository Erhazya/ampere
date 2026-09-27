"""HTTP access to the sources, with responses simulated by httpx2's mock transport."""

from collections.abc import Callable, Iterator

import httpx2
import pytest

from ampere.data.http import USER_AGENT, Fetched, UnexpectedResponse, client, get

URL = "https://example.test/data.json"
JSON = "application/json"
Outcome = httpx2.Response | Exception


def ok(body: bytes | Iterator[bytes] = b'{"a": 1}', content_type: str = JSON) -> httpx2.Response:
    return httpx2.Response(200, content=body, headers={"content-type": content_type})


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


def test_a_response_comes_with_its_url_and_type(sleep: Callable[[float], None]) -> None:
    http, requests = fake([ok()])
    assert get(http, URL, content_type=JSON, sleep=sleep) == Fetched(b'{"a": 1}', URL, JSON)
    assert len(requests) == 1


def test_the_client_names_the_project_and_gives_up_on_a_silent_server() -> None:
    http, requests = fake([ok()])
    get(http, URL, content_type=JSON)
    assert requests[0].headers["user-agent"] == USER_AGENT
    assert "github.com/Erhazya/ampere" in USER_AGENT
    # 30 s to connect, and at most 30 s between two pieces of the answer.
    assert http.timeout.connect == 30 and http.timeout.read == 30


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.Response(429),
        httpx2.Response(500),
        httpx2.Response(502),
        httpx2.Response(503),
        httpx2.Response(504),
        httpx2.Response(522),
        httpx2.ConnectError("connection refused"),
        httpx2.ReadTimeout("no answer"),
        httpx2.ReadError("connection reset"),
        httpx2.RemoteProtocolError("server closed the connection"),
    ],
    ids=str,
)
def test_a_passing_failure_is_tried_again(
    failure: Outcome, sleep: Callable[[float], None], waits: list[float]
) -> None:
    http, requests = fake([failure, ok(b"ok")])
    assert get(http, URL, content_type=JSON, sleep=sleep).content == b"ok"
    assert len(requests) == 2
    assert waits == [1]


@pytest.mark.parametrize(
    ("header", "wait"),
    [("10", 10), ("1000", 60), ("0", 1), ("Wed, 21 Oct 2026 07:28:00 GMT", 1)],
)
def test_a_429_waits_as_long_as_it_asks_60_seconds_at_most(
    header: str, wait: float, sleep: Callable[[float], None], waits: list[float]
) -> None:
    http, _ = fake([httpx2.Response(429, headers={"retry-after": header}), ok()])
    get(http, URL, content_type=JSON, sleep=sleep)
    assert waits == [wait]


def test_after_three_failures_the_error_goes_up_and_each_try_is_logged(
    sleep: Callable[[float], None], waits: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    http, requests = fake([httpx2.Response(503), httpx2.Response(503), httpx2.Response(503)])
    with pytest.raises(httpx2.HTTPStatusError):
        get(http, URL, content_type=JSON, sleep=sleep)
    assert len(requests) == 3
    assert waits == [1, 2]
    assert "try 1 of 3 failed (HTTP 503)" in caplog.text
    assert "try 2 of 3 failed (HTTP 503)" in caplog.text


def test_a_network_error_on_the_last_try_goes_up(sleep: Callable[[float], None]) -> None:
    http, _ = fake([httpx2.ConnectError("a"), httpx2.ConnectError("b"), httpx2.ConnectError("c")])
    with pytest.raises(httpx2.ConnectError):
        get(http, URL, content_type=JSON, sleep=sleep)


@pytest.mark.parametrize("status", [400, 403, 404])
def test_other_errors_stop_at_once_with_the_start_of_their_body(
    status: int, sleep: Callable[[float], None], waits: list[float]
) -> None:
    http, requests = fake([httpx2.Response(status, content=b'{"reason": "no such day"}')])
    with pytest.raises(httpx2.HTTPStatusError) as error:
        get(http, URL, content_type=JSON, sleep=sleep)
    assert len(requests) == 1
    assert waits == []
    assert "no such day" in "\n".join(error.value.__notes__)


def test_a_lasting_network_error_stops_at_once(
    sleep: Callable[[float], None], waits: list[float]
) -> None:
    # A proxy refusing, or a protocol the client does not speak, will not pass by waiting.
    http, requests = fake([httpx2.ProxyError("proxy refused")])
    with pytest.raises(httpx2.ProxyError):
        get(http, URL, content_type=JSON, sleep=sleep)
    assert len(requests) == 1 and waits == []


@pytest.mark.parametrize("status", [201, 202, 204])
def test_only_a_200_goes_on(status: int, sleep: Callable[[float], None]) -> None:
    http, requests = fake([httpx2.Response(status, headers={"content-type": JSON})])
    with pytest.raises(UnexpectedResponse, match=str(status)):
        get(http, URL, content_type=JSON, sleep=sleep)
    assert len(requests) == 1


def test_a_redirect_on_the_same_host_is_followed(sleep: Callable[[float], None]) -> None:
    moved = "https://example.test/moved.json"
    http, _ = fake([httpx2.Response(301, headers={"location": moved}), ok()])
    assert get(http, URL, content_type=JSON, sleep=sleep).url == moved


@pytest.mark.parametrize(
    "location", ["https://elsewhere.test/data.json", "http://example.test/data.json"]
)
def test_a_redirect_to_another_host_or_to_http_is_refused(
    location: str, sleep: Callable[[float], None]
) -> None:
    http, _ = fake([httpx2.Response(302, headers={"location": location}), ok()])
    with pytest.raises(UnexpectedResponse, match="redirected"):
        get(http, URL, content_type=JSON, sleep=sleep)


@pytest.mark.parametrize("received", ["text/html", ""])
def test_another_content_type_is_refused(received: str, sleep: Callable[[float], None]) -> None:
    # A maintenance page, say, instead of the data.
    headers = {"content-type": received} if received else {}
    http, _ = fake([httpx2.Response(200, content=b"<html>", headers=headers)])
    with pytest.raises(UnexpectedResponse, match=received or "without a content type"):
        get(http, URL, content_type=JSON, sleep=sleep)


def test_a_content_type_with_parameters_is_accepted(sleep: Callable[[float], None]) -> None:
    http, _ = fake([ok(content_type="application/json; charset=utf-8")])
    fetched = get(http, URL, content_type=JSON, sleep=sleep)
    assert fetched.content_type == "application/json; charset=utf-8"


def test_an_empty_body_is_refused_unless_allowed(sleep: Callable[[float], None]) -> None:
    http, _ = fake([ok(b""), ok(b"")])
    with pytest.raises(UnexpectedResponse, match="empty"):
        get(http, URL, content_type=JSON, sleep=sleep)
    assert get(http, URL, content_type=JSON, allow_empty=True, sleep=sleep).content == b""


@pytest.mark.parametrize(
    "body",
    [b"12345678901", iter([b"12345", b"67890", b"1"])],
    ids=["announced", "streamed"],
)
def test_a_body_larger_than_the_limit_is_refused(
    body: bytes | Iterator[bytes], sleep: Callable[[float], None]
) -> None:
    http, _ = fake([ok(body)])
    with pytest.raises(UnexpectedResponse, match="more than 10 bytes"):
        get(http, URL, content_type=JSON, max_bytes=10, sleep=sleep)


def test_the_tries_stop_at_the_deadline(sleep: Callable[[float], None]) -> None:
    # The clock says 299.5 s have passed when the first try fails: waiting 1 s would pass 300 s.
    times = iter([0.0, 299.5])
    http, requests = fake([httpx2.Response(503), ok()])
    with pytest.raises(TimeoutError, match="300 s"):
        get(http, URL, content_type=JSON, sleep=sleep, clock=lambda: next(times))
    assert len(requests) == 1
