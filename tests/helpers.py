"""In-process `brief()` services for the tests.

Each service is the repository's example (`examples/finance_coach.py`) served over MCP
streamable HTTP by the real `mcp` server stack, reached through an ASGI transport: real
protocol, real client, no socket and no external network. A `Variant` changes what the example
answers (slow, garbage, oversize, stale...) or what the HTTP layer in front of it does (401,
500, a redirect). All hosts, tokens and texts are synthetic.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from mcp_brief.config import ServiceConfig

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.UTC)
TOKEN = "synthetic-token-0123456789"  # noqa: S105
BODY_MARKER = "response-body-that-must-never-be-echoed"


def _example() -> Callable[..., dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "finance_coach_example", ROOT / "examples" / "finance_coach.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    brief: Callable[..., dict[str, Any]] = module.brief
    return brief


def stamp(delta: dt.timedelta = dt.timedelta(0)) -> str:
    return (NOW + delta).isoformat().replace("+00:00", "Z")


def example_brief(**changes: Any) -> dict[str, Any]:
    """What the example service answers, with its dates moved to `NOW` (the plugin's clock)."""
    document = _example()(schema_version=1)
    document["generated_at"] = stamp()
    document["valid_until"] = stamp(dt.timedelta(hours=12))
    document.update(changes)
    return document


@dataclass
class Variant:
    document: dict[str, Any] = field(default_factory=example_brief)
    text: str | None = None
    """Answer with this text instead of a document (a tool that returns garbage)."""
    fail: bool = False
    """The tool raises (a tool error)."""
    delay: float = 0.0
    token: str | None = None
    """Require `Authorization: Bearer <token>`, else answer 401."""
    status: int | None = None
    """Answer every request with this HTTP status and a body holding `BODY_MARKER`."""
    redirect: str | None = None
    raw: bytes | None = None
    """Answer 200 with these bytes as the body (not JSON-RPC)."""
    headers: dict[str, str] = field(default_factory=dict)
    """Extra headers on the raw or redirect answer."""
    seen: list[dict[str, str]] = field(default_factory=list)
    """The request headers this service received, in order."""


def _server(variant: Variant) -> MCPServer:
    server = MCPServer("example")

    async def document(schema_version: int = 1) -> dict[str, Any]:
        if variant.fail:
            raise ValueError(BODY_MARKER)
        if variant.delay:
            await anyio.sleep(variant.delay)
        return variant.document

    async def text(schema_version: int = 1) -> str:
        return variant.text or ""

    server.add_tool(text if variant.text is not None else document, name="brief")
    return server


class _Gate:
    """The HTTP layer in front of the MCP app."""

    def __init__(self, app: Any, variant: Variant) -> None:
        self.app = app
        self.variant = variant

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        variant = self.variant
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        variant.seen.append(headers)
        answer: tuple[int, list[tuple[bytes, bytes]], bytes] | None = None
        if variant.token is not None and headers.get("authorization") != f"Bearer {variant.token}":
            answer = (401, [], BODY_MARKER.encode())
        elif variant.redirect is not None:
            answer = (307, [(b"location", variant.redirect.encode())], b"")
        elif variant.status is not None:
            answer = (variant.status, [], BODY_MARKER.encode())
        elif variant.raw is not None:
            answer = (200, [(b"content-type", b"application/json")], variant.raw)
        if answer is None:
            await self.app(scope, receive, send)
            return
        status, extra, body = answer
        extra += [(k.encode(), v.encode()) for k, v in variant.headers.items()]
        await send({"type": "http.response.start", "status": status, "headers": extra})
        await send({"type": "http.response.body", "body": body})


@contextlib.asynccontextmanager
async def serve(
    services: dict[str, Variant],
) -> AsyncIterator[Callable[[ServiceConfig], httpx2.AsyncBaseTransport]]:
    """A transport factory routing each host of `services` to its in-process service. A host
    that is not served refuses the connection."""
    apps: dict[str, Any] = {}
    async with contextlib.AsyncExitStack() as stack:
        for host, variant in services.items():
            app = _server(variant).streamable_http_app(
                json_response=True,
                stateless_http=True,
                transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            )
            await stack.enter_async_context(app.router.lifespan_context(app))
            apps[host] = _Gate(app, variant)

        def factory(service: ServiceConfig) -> httpx2.AsyncBaseTransport:
            app = apps.get(service.host)
            if app is None:
                return httpx2.MockTransport(_refuse)
            return httpx2.ASGITransport(app=app)

        yield factory


def _refuse(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("refused", request=request)
