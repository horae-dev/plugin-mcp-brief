"""Shared fixtures: the plugin with a fixed clock and in-process services."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anyio
import pytest
from horae_sdk.testing import Replayed, call

from helpers import NOW, TOKEN, Variant, serve
from mcp_brief import build

TOOL = "mcp-brief.collect"
Run = Callable[..., Replayed]


def service(host: str, **fields: Any) -> dict[str, Any]:
    """A `services` entry for `https://<host>/mcp`; its id is the first label of the host."""
    sid = fields.pop("id", host.split(".")[0])
    return {"id": sid, "url": f"https://{host}/mcp", **fields}


@pytest.fixture
def run() -> Run:
    """`run(variants, services, **params)`: call the tool against the in-process `variants`
    (host -> Variant), with those hosts approved unless `network` says otherwise."""

    def go(
        variants: dict[str, Variant],
        services: list[dict[str, Any]],
        *,
        network: list[str] | str | None = "all",
        environ: dict[str, str] | None = None,
        **params: Any,
    ) -> Replayed:
        async def inner() -> Replayed:
            async with serve(variants) as transport:
                plugin = build(
                    transport,
                    environ={"MCP_BRIEF_TOKEN_1": TOKEN} if environ is None else environ,
                    clock=lambda: NOW,
                )
                approved = list(variants) if network == "all" else network
                return await call(
                    plugin,
                    TOOL,
                    {"services": services, **params},
                    network=approved,
                )

        return anyio.run(inner)

    return go
