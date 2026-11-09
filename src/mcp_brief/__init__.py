"""Horae plugin: call a brief() tool on any MCP service. Holds the brief() contract."""

from mcp_brief.contract import SCHEMA_ID, SCHEMA_VERSION, Brief, brief_json_schema
from mcp_brief.plugin import COMMAND, NAME, VERSION, build, manifest

__all__ = [
    "COMMAND",
    "NAME",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "VERSION",
    "Brief",
    "brief_json_schema",
    "build",
    "manifest",
]
