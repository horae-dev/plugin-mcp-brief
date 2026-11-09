# SPDX-License-Identifier: Apache-2.0
"""A brief as a note section: Markdown where every service string is escaped data.

Every string from a service goes through `horae_sdk.tasks.escape_title` (no link, tag,
wikilink, embed, math, Dataview or Templater construct, no Tasks signifier, no control
character), then a block marker is defused. It is always placed after a fixed label (`**Summary:**`,
`- **Warn:**`), never at the start of a line, so it cannot open a heading, a list, a quote or a
checkbox. A URL becomes a link only under one of the service's `link_prefix` entries, and its
destination is made inert by `horae_sdk.tasks.link_destination`.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from horae_sdk import tasks

from mcp_brief.contract import Brief

MAX_TEXT = 500
_MARKER = re.compile(r"(?i)(horae):(?=\s*(?:begin|end))")
_DOT_SEGMENT = re.compile(r"(?i)(?:^|/)(?:\.|%2e){1,2}(?:/|$)")


def data(text: str) -> str:
    """`text` from a service as inert inline Markdown."""
    escaped = tasks.escape_title(text, max_length=MAX_TEXT)
    return _MARKER.sub(r"\1\\:", escaped)


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").lower()
    return parts.scheme, host, parts.port if parts.port is not None else 443


def under(url: str, prefixes: list[str]) -> bool:
    """Whether `url` falls under one of the trusted `prefixes`: the same origin (host compared
    case-insensitively, a trailing dot and `:443` ignored) and a path that starts with the
    prefix's path, case-sensitively, with no dot segment (so `/a/../b` is never under `/a/`)."""
    try:
        if _DOT_SEGMENT.search(urlsplit(url).path):
            return False
        return any(
            _origin(url) == _origin(prefix) and urlsplit(url).path.startswith(urlsplit(prefix).path)
            for prefix in prefixes
        )
    except ValueError:  # a port out of range, say: not under any prefix
        return False


def link(label: str, url: str, prefixes: list[str]) -> str:
    """`[label](url)` when `url` is trusted, else the label alone (no URL is shown)."""
    if under(url, prefixes) and tasks.is_task_link(url):
        return f"[{data(label)}]({tasks.link_destination(url)})"
    return data(label)


def section(brief: Brief, prefixes: list[str], *, stale: bool) -> str:
    """The note section of a brief that can be shown (a stale one says so and nothing else)."""
    lines = [f"### {data(brief.service.name)}", ""]
    if stale:
        lines.append(
            f"**Stale:** this brief expired at {brief.valid_until} and is not trusted. "
            f"Last headline: {data(brief.headline)}"
        )
        return "\n".join(lines) + "\n"
    state = brief.status.state
    lines.append(f"**Summary:** {data(brief.headline)}")
    if state != "ok":
        lines.extend(["", f"**Status:** {state} - {data(brief.status.reason or state)}"])
    if state == "error":
        return "\n".join(lines) + "\n"
    if brief.alerts:
        lines.append("")
        for alert in brief.alerts:
            text = data(alert.title)
            if alert.body:
                text += f" - {data(alert.body)}"
            if alert.link and under(alert.link, prefixes) and tasks.is_task_link(alert.link):
                text += f" ([link]({tasks.link_destination(alert.link)}))"
            lines.append(f"- **{alert.severity.capitalize()}:** {text}")
    if brief.links:
        lines.extend(
            ["", "**Links:** " + " | ".join(link(x.label, x.url, prefixes) for x in brief.links)]
        )
    return "\n".join(lines) + "\n"


def unavailable(service_id: str, reason: str) -> str:
    """The section of a service that could not be read: fixed text only."""
    return f"### {service_id}\n\n**Unavailable:** {reason}\n"
