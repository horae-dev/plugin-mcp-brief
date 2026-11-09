# SPDX-License-Identifier: Apache-2.0
"""Call `brief()` on one service over MCP streamable HTTP.

Guarantees, all enforced here and tested with in-process servers:

- one **total deadline** for connecting, the handshake and the call;
- the **response cap** is enforced on the bytes of each HTTP response while they arrive, so an
  oversized body is never buffered or parsed (and a body with a `Content-Encoding` is refused:
  the cap would count compressed bytes);
- the request goes to the configured origin only. The MCP transport already follows a redirect
  only within the origin; this transport refuses to *send* anything else, so a credentialed
  request can never reach another host;
- a failure is a `BriefFailure` with a short fixed reason. Nothing from the response (body,
  status text, headers) and nothing from the request (token, URL) is put in it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import TextContent

from mcp_brief.config import ServiceConfig

TransportFactory = Callable[[ServiceConfig], httpx2.AsyncBaseTransport]
"""Builds the HTTP transport of one service; tests route the host to an in-process server."""

TOOL = "brief"
SCHEMA_VERSION_ARGUMENT = 1

REASONS = (
    "auth_failed",
    "connection_failed",
    "http_error",
    "invalid_json",
    "oversize",
    "protocol_error",
    "redirect_refused",
    "request_failed",
    "timeout",
    "tool_error",
)
"""The fixed reasons this module raises."""


class BriefFailure(Exception):
    """The call failed; `reason` is one of `REASONS` (or another module's fixed code)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def default_transport(_: ServiceConfig) -> httpx2.AsyncBaseTransport:
    """The real network: TLS verified, no proxy variable read from the environment."""
    return httpx2.AsyncHTTPTransport(trust_env=False, retries=0)


class _Trace:
    """What the guarding transport saw; `reason` is set first and ends the call at once."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self.last_status: int | None = None
        self.scope: anyio.CancelScope | None = None

    def trip(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
        if self.scope is not None:
            self.scope.cancel()


class _Refused(Exception):
    """Raised inside the transport after `_Trace.trip`; never leaves this module."""


class _Capped(httpx2.AsyncByteStream):
    def __init__(self, inner: httpx2.AsyncByteStream, cap: int, trace: _Trace) -> None:
        self._inner = inner
        self._cap = cap
        self._trace = trace

    async def __aiter__(self) -> AsyncIterator[bytes]:
        seen = 0
        async for chunk in self._inner:
            seen += len(chunk)
            if seen > self._cap:
                self._trace.trip("oversize")
                raise _Refused
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _Guarded(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx2.AsyncBaseTransport,
        origin: tuple[str, str, int | None],
        cap: int,
        trace: _Trace,
    ) -> None:
        self._inner = inner
        self._origin = origin
        self._cap = cap
        self._trace = trace

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if (url.scheme, url.host, url.port) != self._origin:
            self._trace.trip("redirect_refused")
            raise _Refused
        response = await self._inner.handle_async_request(request)
        status = response.status_code
        self._trace.last_status = status
        if status in (401, 403):
            await response.aclose()
            self._trace.trip("auth_failed")
            raise _Refused
        length = response.headers.get("content-length", "")
        encoding = response.headers.get("content-encoding", "identity").lower()
        if encoding not in ("", "identity"):
            await response.aclose()
            self._trace.trip("protocol_error")
            raise _Refused
        if length.isdigit() and int(length) > self._cap:
            await response.aclose()
            self._trace.trip("oversize")
            raise _Refused
        assert isinstance(response.stream, httpx2.AsyncByteStream)  # noqa: S101
        return httpx2.Response(
            status,
            headers=response.headers,
            stream=_Capped(response.stream, self._cap, self._trace),
            request=request,
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def _leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for member in error.exceptions for leaf in _leaves(member)]
    return [error]


def _classify(error: BaseException, trace: _Trace) -> str:
    """A fixed reason for `error`. What the guard saw wins over the exception it caused."""
    if trace.reason is not None:
        return trace.reason
    leaves = _leaves(error)
    if any(isinstance(leaf, httpx2.TimeoutException | TimeoutError) for leaf in leaves):
        return "timeout"
    if trace.last_status is not None and 300 <= trace.last_status < 400:
        return "redirect_refused"
    if any(isinstance(leaf, httpx2.TransportError | OSError) for leaf in leaves):
        return "connection_failed"
    if trace.last_status is not None and trace.last_status >= 400:
        return "http_error"
    if any(isinstance(leaf, MCPError) for leaf in leaves):
        return "protocol_error"
    return "request_failed"


async def fetch_brief(
    service: ServiceConfig,
    token: str | None,
    *,
    max_response_bytes: int,
    transport: httpx2.AsyncBaseTransport,
) -> Any:
    """The JSON document `brief(schema_version=1)` returned, not yet validated.

    Raises `BriefFailure` (never anything else) with a fixed reason."""
    trace = _Trace()
    url = httpx2.URL(service.url)
    origin = (url.scheme, url.host, url.port)
    guarded = _Guarded(transport, origin, max_response_bytes, trace)
    headers = {"Accept-Encoding": "identity"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    result = None
    try:
        with anyio.move_on_after(service.timeout) as scope:
            trace.scope = scope
            async with (
                httpx2.AsyncClient(
                    transport=guarded,
                    headers=headers,
                    timeout=httpx2.Timeout(service.timeout),
                    follow_redirects=False,
                    trust_env=False,
                ) as http,
                Client(
                    streamable_http_client(service.url, http_client=http),
                    cache=None,
                    read_timeout_seconds=service.timeout,
                ) as client,
            ):
                result = await client.call_tool(TOOL, {"schema_version": SCHEMA_VERSION_ARGUMENT})
    except (Exception, BaseExceptionGroup) as error:  # every failure becomes a fixed reason
        raise BriefFailure(_classify(error, trace)) from None
    if trace.reason is not None:
        raise BriefFailure(trace.reason)
    if scope.cancelled_caught or result is None:
        raise BriefFailure("timeout")
    if result.is_error:
        raise BriefFailure("tool_error")
    structured = result.structured_content
    text: str | None = None
    if isinstance(structured, dict):
        wrapped = structured.get("result")
        if set(structured) != {"result"} or not isinstance(wrapped, str):
            return structured
        text = wrapped  # a tool that returns the document as a JSON string
    else:
        text = next((b.text for b in result.content if isinstance(b, TextContent)), None)
    try:
        return json.loads(text or "")
    except (ValueError, RecursionError):
        raise BriefFailure("invalid_json") from None
