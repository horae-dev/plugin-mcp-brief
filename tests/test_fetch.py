"""The HTTP guards of `fetch_brief`, on a mock transport (no server, no network)."""

from __future__ import annotations

import gzip
import json
from collections.abc import AsyncIterator, Callable

import anyio
import httpx2
import pytest

from mcp_brief.config import ServiceConfig
from mcp_brief.fetch import (
    BriefFailure,
    _Guarded,
    _Refused,
    _Trace,
    default_transport,
    fetch_brief,
)

SERVICE = ServiceConfig.model_validate(
    {"id": "svc", "url": "https://svc.example.test/mcp", "timeout": 2}
)
BODY = "response-body-that-must-never-be-echoed"
Handler = Callable[[httpx2.Request], httpx2.Response]


def fetch(handler: Handler, cap: int = 64 * 1024) -> str:
    async def go() -> str:
        await fetch_brief(
            SERVICE,
            "tok-0123456789",
            max_response_bytes=cap,
            transport=httpx2.MockTransport(handler),
        )
        return "returned"

    try:
        return anyio.run(go)
    except BriefFailure as failure:
        return failure.reason


def test_a_declared_length_over_the_cap_is_refused_before_reading() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-length": "70000"}, content=b"{}")

    assert fetch(handler) == "oversize"


def test_a_streamed_body_over_the_cap_is_cut_off() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"x" * 5000)

    assert fetch(handler, cap=1024) == "oversize"


def test_a_compressed_body_is_refused_because_the_cap_would_count_compressed_bytes() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        body = gzip.compress(json.dumps({"x": "a" * 100_000}).encode())
        return httpx2.Response(200, headers={"content-encoding": "gzip"}, content=body)

    assert fetch(handler) == "protocol_error"


def test_the_request_asks_for_an_uncompressed_body_and_carries_the_token() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(500, content=b"nope")

    assert fetch(handler) == "http_error"
    assert seen[0].headers["accept-encoding"] == "identity"
    assert seen[0].headers["authorization"] == "Bearer tok-0123456789"


def test_a_cross_origin_request_is_never_sent() -> None:
    sent: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url))
        return httpx2.Response(307, headers={"location": "https://evil.example.test/mcp"})

    assert fetch(handler) == "redirect_refused"
    assert set(sent) == {"https://svc.example.test/mcp"}  # the probe and the handshake


def test_a_dropped_connection_is_a_connection_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom", request=request)

    assert fetch(handler) == "connection_failed"


def test_a_server_that_never_answers_times_out() -> None:
    async def hang() -> None:
        await anyio.sleep(30)

    class Silent(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            await hang()
            raise AssertionError

    async def go() -> str:
        try:
            await fetch_brief(
                ServiceConfig.model_validate(
                    {"id": "svc", "url": "https://svc.example.test/mcp", "timeout": 1}
                ),
                None,
                max_response_bytes=1024,
                transport=Silent(),
            )
        except BriefFailure as failure:
            return failure.reason
        return "returned"

    assert anyio.run(go) == "timeout"


@pytest.mark.parametrize("body", [b"", b"not json", b"[1, 2"])
def test_a_body_that_is_not_a_json_rpc_message_is_a_protocol_error(body: bytes) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=body)

    assert fetch(handler) in {"protocol_error", "request_failed"}


def test_the_default_transport_verifies_tls_and_ignores_proxy_variables() -> None:
    async def go() -> None:
        transport = default_transport(SERVICE)
        assert isinstance(transport, httpx2.AsyncHTTPTransport)
        await transport.aclose()

    anyio.run(go)


def test_a_chunked_body_over_the_cap_is_cut_off_without_a_declared_length() -> None:
    class Endless(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(100):
                yield b"x" * 100

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "application/json"}, stream=Endless())

    assert fetch(handler, cap=1024) == "oversize"


def test_a_read_timeout_is_a_timeout() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    assert fetch(handler) == "timeout"


def test_an_unexpected_error_is_a_fixed_reason() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise RuntimeError(BODY)

    assert fetch(handler) == "request_failed"


def test_the_guard_does_not_send_a_request_to_another_origin() -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200)

    async def go() -> None:
        trace = _Trace()
        guard = _Guarded(
            httpx2.MockTransport(handler), ("https", "svc.example.test", None), 10, trace
        )
        with pytest.raises(_Refused):
            await guard.handle_async_request(httpx2.Request("POST", "https://evil.example.test/"))
        with pytest.raises(_Refused):
            await guard.handle_async_request(httpx2.Request("POST", "http://svc.example.test/"))
        assert trace.reason == "redirect_refused"

    anyio.run(go)
    assert sent == []
