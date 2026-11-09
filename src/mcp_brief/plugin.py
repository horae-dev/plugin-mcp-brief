# SPDX-License-Identifier: Apache-2.0
"""The plugin: the source `mcp-brief.collect`, which calls `brief()` on each configured MCP
service and turns the answers into note sections, tasks and urgent pushes.

The core's plugin host starts this module as an MCP server over stdio (`python -m mcp_brief`).

**Trusted and untrusted.** The tool's parameters come from the pipeline (trusted): the services
(URL, token secret *name*, timeout, link prefixes), the limits and `allowed_hosts`. A token is
read from its declared optional secret at call time and goes only into the `Authorization`
header of that service's requests. Everything a service returns is untrusted: it is validated
against the `Brief` model and then only *fitted into* intents the plugin builds
(`docs/brief-contract.md`, "How Horae maps a brief onto its intents"); a service chooses
neither an intent type, a note path, a block id, a recipient nor a host.

**Failures.** A service that cannot be read is *degraded* with a short fixed reason; the others
still flow, and the call returns normally (it never fails the step). Its section is replaced
with a fixed "unavailable" line, so an old brief is not shown as current.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple, TypedDict

import anyio
from horae_sdk import Call, Output, Permissions, Plugin, PluginError, intents
from horae_sdk.intents import IntentError
from pydantic import ValidationError

from mcp_brief import render
from mcp_brief.config import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_MAX_SKEW_SECONDS,
    MAX_RESPONSE_BYTES,
    MAX_SERVICES,
    MAX_SKEW_SECONDS,
    MIN_RESPONSE_BYTES,
    SERVICE_ID,
    ServiceConfig,
    parse_allowed_hosts,
    parse_service,
)
from mcp_brief.contract import Alert, Brief, Task
from mcp_brief.fetch import BriefFailure, TransportFactory, default_transport, fetch_brief

NAME = "mcp-brief"
"""The plugin's name, also the namespace of its ref (`mcp-brief.collect`)."""
VERSION = "0.1.0"
HORAE = ">=0,<0.2"
HOMEPAGE = "https://github.com/horae-dev/plugin-mcp-brief"
COMMAND = ["python3", "-m", "mcp_brief"]
"""`runtime.command` of `plugin.yaml`. When installing, pin an absolute interpreter that has
this package (the host's `PATH` is fixed and its working directory empty)."""
HOSTS: tuple[str, ...] = ()
"""Hosts declared in `plugin.yaml`: none. The services are the operator's own, so each host is
added to `permissions.network` of the installed copy and approved in `horae.lock` (README,
"Hosts"); `allowed_hosts` is the fallback for a core that sends no list."""
TOKEN_SECRETS = tuple(f"MCP_BRIEF_TOKEN_{n}" for n in range(1, MAX_SERVICES + 1))
"""The declared optional secrets: one bearer token per service, chosen by `token_secret`."""
PERMISSIONS = Permissions(
    network=list(HOSTS),
    optional_secrets=list(TOKEN_SECRETS),
    intents=["note.replace_owned_block", "task.create", "notify.push"],
)
"""Declare the least you need. Every token is optional (core decision 56): a service without
`token_secret` is called unauthenticated, and one whose secret is missing is degraded with
`secret_missing`."""

REF = f"{NAME}.collect"
_log = logging.getLogger("mcp_brief")


class ServiceResult(TypedDict):
    id: str
    status: str
    reason: str | None
    brief_state: str | None
    tasks: int
    pushes: int


class _Done:
    """What one service contributes: its result row and the intents it proposes."""

    def __init__(self, service_id: str) -> None:
        self.row: ServiceResult = {
            "id": service_id,
            "status": "ok",
            "reason": None,
            "brief_state": None,
            "tasks": 0,
            "pushes": 0,
        }
        self.intents: list[dict[str, Any]] = []

    def degrade(self, reason: str, section: str | None = None) -> _Done:
        self.row["status"] = "degraded"
        self.row["reason"] = reason
        self.intents = [
            intents.note_replace_owned_block(
                f"brief-{self.row['id']}", section or render.unavailable(self.row["id"], reason)
            )
        ]
        return self


def build(
    transport: TransportFactory | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> Plugin:
    """The plugin, calling services through `transport(service)` (default: the real network),
    reading tokens from `environ` (default `os.environ`, at call time) and the time from
    `clock` (default: now, UTC)."""
    make_transport = transport if transport is not None else default_transport
    now = clock if clock is not None else _utcnow
    plugin = Plugin(
        NAME,
        version=VERSION,
        permissions=PERMISSIONS,
        description="Call a brief() tool on MCP services: morning-note sections, tasks, alerts.",
    )

    @plugin.source(REF)
    async def collect(
        call: Call,
        services: list[dict[str, Any]],
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
        allowed_hosts: list[str] | None = None,
    ) -> Output:
        """One section per service (block `brief-<id>`), `task.create` per task and
        `notify.push` per `crit` alert. A service that fails is degraded; the step never is."""
        if not MIN_RESPONSE_BYTES <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise PluginError(
                "config_invalid: max_response_bytes is "
                f"{MIN_RESPONSE_BYTES} to {MAX_RESPONSE_BYTES}"
            )
        if not 0 <= max_skew_seconds <= MAX_SKEW_SECONDS:
            raise PluginError(f"config_invalid: max_skew_seconds is 0 to {MAX_SKEW_SECONDS}")
        fallback = frozenset(HOSTS) | parse_allowed_hosts(allowed_hosts)
        raws = _raw_services(services)
        env = os.environ if environ is None else environ
        limits = _Limits(max_response_bytes, dt.timedelta(seconds=max_skew_seconds))
        results: list[_Done] = [_Done(str(raw["id"])) for raw in raws]

        async def one(index: int, raw: dict[str, Any]) -> None:
            try:
                results[index] = await _service(
                    raw, call, fallback, env, make_transport, now(), limits
                )
            except Exception as error:  # a bug must not cost the other services
                _log.warning("service %s failed (%s)", raw["id"], type(error).__name__)
                results[index] = _Done(str(raw["id"])).degrade("request_failed")

        async with anyio.create_task_group() as group:
            for index, raw in enumerate(raws):
                group.start_soon(one, index, raw)
        rows = [item.row for item in results]
        data = {"complete": all(row["status"] == "ok" for row in rows), "services": rows}
        return Output(data, intents=[intent for item in results for intent in item.intents])

    return plugin


def manifest() -> dict[str, Any]:
    """The `plugin.yaml` document (`python -m mcp_brief --manifest`)."""
    return build().manifest(command=COMMAND, horae=HORAE, license="Apache-2.0", homepage=HOMEPAGE)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class _Limits(NamedTuple):
    max_response_bytes: int
    skew: dt.timedelta


def _raw_services(value: object) -> list[dict[str, Any]]:
    """The services as given, each with a usable unique `id` (else the list is ambiguous)."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SERVICES:
        raise PluginError(f"config_invalid: services must list 1 to {MAX_SERVICES} services")
    seen: set[str] = set()
    for raw in value:
        service_id = raw.get("id") if isinstance(raw, dict) else None
        if not isinstance(service_id, str) or not SERVICE_ID.fullmatch(service_id):
            raise PluginError("config_invalid: each service needs an id (lowercase slug, 32 max)")
        if service_id in seen:
            raise PluginError("config_invalid: service ids must be unique")
        seen.add(service_id)
    return list(value)


def _unapproved(call: Call, service: ServiceConfig, fallback: frozenset[str]) -> bool:
    """The host is refused: not in `horae.lock`, or (an older core sent no list) not in the
    declared hosts and `allowed_hosts`."""
    approved = call.host_allowed(service.url)
    if approved is None:
        return service.host not in fallback
    return not approved


async def _service(
    raw: dict[str, Any],
    call: Call,
    fallback: frozenset[str],
    env: Mapping[str, str],
    make_transport: TransportFactory,
    now: dt.datetime,
    limits: _Limits,
) -> _Done:
    done = _Done(raw["id"])
    service = parse_service(raw)
    if service is None:
        return done.degrade("config_invalid")
    if _unapproved(call, service, fallback):
        return done.degrade("host_not_approved")
    token = None
    if service.token_secret is not None:
        token = env.get(service.token_secret)
        if token is None or token == "":
            return done.degrade("secret_missing")
        if not token.isascii() or not token.isprintable() or " " in token:
            return done.degrade("secret_invalid")
    try:
        document = await fetch_brief(
            service,
            token,
            max_response_bytes=limits.max_response_bytes,
            transport=make_transport(service),
        )
    except BriefFailure as failure:
        return done.degrade(failure.reason)
    try:
        brief = Brief.model_validate(document)
    except ValidationError:
        return done.degrade("schema_violation")
    return _map(done, service, brief, now, limits.skew)


def _map(
    done: _Done, service: ServiceConfig, brief: Brief, now: dt.datetime, skew: dt.timedelta
) -> _Done:
    """The intents of a valid brief (docs/brief-contract.md, "Rules the plugin applies")."""
    if len({alert.key for alert in brief.alerts}) != len(brief.alerts) or len(
        {task.id for task in brief.tasks}
    ) != len(brief.tasks):
        return done.degrade("schema_violation")
    if dt.datetime.fromisoformat(brief.generated_at) > now + skew:
        return done.degrade("clock_skew")
    block = f"brief-{service.id}"
    done.row["brief_state"] = brief.status.state
    if now > dt.datetime.fromisoformat(brief.valid_until):
        return done.degrade("stale", render.section(brief, service.link_prefix, stale=True))
    content = render.section(brief, service.link_prefix, stale=False)
    if brief.status.state == "error":
        # An `error` brief's items are ignored, whatever the document holds.
        return done.degrade("service_error", content)
    done.intents.append(intents.note_replace_owned_block(block, content))
    for task in brief.tasks:
        done.intents.append(_task(service, task))
    for alert in brief.alerts:
        if alert.severity == "crit":
            done.intents.append(_push(service, brief, alert))
    done.row["tasks"] = len(brief.tasks)
    done.row["pushes"] = sum(1 for alert in brief.alerts if alert.severity == "crit")
    return done


def _task(service: ServiceConfig, task: Task) -> dict[str, Any]:
    key = f"{service.id}:{task.id}"
    link = task.link if task.link and render.under(task.link, service.link_prefix) else None
    try:
        return intents.task_create(task.title, key=key, due=task.due, link=link)
    except IntentError:  # a link the SDK refuses (an IP host, say) is dropped, not the task
        return intents.task_create(task.title, key=key, due=task.due)


def _push(service: ServiceConfig, brief: Brief, alert: Alert) -> dict[str, Any]:
    key = f"{service.id}:{alert.key}"
    link = alert.link if alert.link and render.under(alert.link, service.link_prefix) else None
    return intents.notify_push(alert.title, title=brief.service.name, key=key, link=link)
