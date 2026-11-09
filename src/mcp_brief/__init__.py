"""Horae plugin: call a brief() tool on any MCP service. Holds the brief() contract."""

from mcp_brief.contract import SCHEMA_ID, SCHEMA_VERSION, Brief, brief_json_schema

__all__ = ["SCHEMA_ID", "SCHEMA_VERSION", "Brief", "brief_json_schema"]
