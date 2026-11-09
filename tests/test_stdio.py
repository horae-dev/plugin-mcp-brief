"""The plugin runs as an MCP server over stdio, as the core's plugin host starts it: with only
declared secrets in its environment. No network: the host is not approved, so nothing is
contacted."""

import sys

import anyio
from horae_sdk.testing import Replayed, read_result
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from mcp_brief import COMMAND, NAME

TOOL = f"{NAME}.collect"
SERVICES = [{"id": "finance", "url": "https://finance.example.test/mcp"}]


async def serve_and_call() -> tuple[set[str], Replayed]:
    server = StdioServerParameters(command=sys.executable, args=COMMAND[1:], env={})
    async with Client(server, read_timeout_seconds=30) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        # `_meta["horae/network"]: []`, as a core sends for a plugin with no approved host.
        answer = await client.call_tool(TOOL, {"services": SERVICES}, meta={"horae/network": []})  # type: ignore[arg-type]
    return tools, read_result(answer)


def test_serves_over_stdio() -> None:
    tools, replayed = anyio.run(serve_and_call)
    assert tools == {TOOL}
    assert replayed.error is None
    assert replayed.data["services"][0]["reason"] == "host_not_approved"
    assert [i["block"] for i in replayed.intents] == ["brief-finance"]
