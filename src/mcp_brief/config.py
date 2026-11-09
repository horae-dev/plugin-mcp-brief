# SPDX-License-Identifier: Apache-2.0
"""The plugin's trusted configuration: the services to call and the limits.

Everything here comes from the pipeline (the operator), never from a service. A service that
is badly configured is *degraded* (`config_invalid`), not a failed step; only a list that
cannot be told apart (no usable `id`) is a `PluginError`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import urlsplit

from horae_sdk.protocol import url_host
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_SERVICES = 8
"""At most 8 services: each proposes up to 1 section, 10 tasks and 10 pushes, and the core
refuses a step over 256 intents."""
MAX_LINK_PREFIXES = 8
DEFAULT_TIMEOUT = 10.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 60.0
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024
"""The contract's documented cap (docs/brief-contract.md): 64 KiB, counted on the HTTP body."""
MIN_RESPONSE_BYTES = 1024
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_MAX_SKEW_SECONDS = 300
"""The contract's documented tolerance: a `generated_at` more than 5 minutes ahead is refused."""
MAX_SKEW_SECONDS = 3600
MAX_ALLOWED_HOSTS = 16
DEFAULT_MAX_PUSHES = 3
"""Pushes per service per run: a service picks its alert keys, so it could flood the phone."""
MAX_PUSHES = 10
DEFAULT_MAX_TASKS = 10
"""New tasks per service per run: a service picks its task ids, so it could flood the vault."""
MAX_TASKS = 10
DEFAULT_MAX_VALIDITY_HOURS = 48
"""Longest `valid_until - generated_at` accepted, so a brief cannot stay fresh forever."""
MIN_VALIDITY_HOURS = 1
MAX_VALIDITY_HOURS = 168

SERVICE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
SECRET_NAME = re.compile(r"MCP_BRIEF_[A-Z0-9_]{1,100}")
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
_DOT_SEGMENT = re.compile(r"(?i)(?:^|/)(?:\.|%2e){1,2}(?:/|$)")


def _https_url(value: str, *, directory: bool) -> str:
    parts = urlsplit(value)
    if (
        len(value) > 2048
        or (host := url_host(value)) is None  # https, no user info, space or backslash
        or not HOST.fullmatch(host)  # a lowercase DNS name, never an IP address
        or host == "localhost"
        or host.endswith(".localhost")
        or parts.query
        or parts.fragment
        or "?" in value
        or "#" in value
        or "%" in parts.netloc
        or _DOT_SEGMENT.search(parts.path)
    ):
        raise ValueError("not a plain https URL")
    if directory and not value.endswith("/"):
        raise ValueError("a link_prefix ends with /")
    return value


class ServiceConfig(BaseModel):
    """One service to call (see the README)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=SERVICE_ID.pattern)]
    url: str
    token_secret: Annotated[str | None, Field(default=None, pattern=SECRET_NAME.pattern)]
    timeout: Annotated[float, Field(ge=MIN_TIMEOUT, le=MAX_TIMEOUT, allow_inf_nan=False)] = (
        DEFAULT_TIMEOUT
    )
    link_prefix: Annotated[list[str], Field(default_factory=list, max_length=MAX_LINK_PREFIXES)]

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _https_url(value, directory=False)

    @field_validator("link_prefix")
    @classmethod
    def _prefixes(cls, value: list[str]) -> list[str]:
        return [_https_url(prefix, directory=True) for prefix in value]

    @property
    def host(self) -> str:
        host = url_host(self.url)
        assert host is not None  # noqa: S101 (checked by the validator)
        return host


def parse_service(raw: dict[str, Any]) -> ServiceConfig | None:
    """The service, or `None` when its fields are invalid (never says which: a value can be a
    secret someone pasted in the wrong place)."""
    try:
        return ServiceConfig.model_validate(raw)
    except ValidationError:
        return None


def parse_allowed_hosts(value: object) -> frozenset[str]:
    """`allowed_hosts` parameter: lowercase DNS names (no IP, no port, no `localhost`)."""
    from horae_sdk import PluginError

    if value is None:
        return frozenset()
    if not isinstance(value, list) or len(value) > MAX_ALLOWED_HOSTS:
        raise PluginError(f"config_invalid: allowed_hosts must list at most {MAX_ALLOWED_HOSTS}")
    for host in value:
        if (
            not isinstance(host, str)
            or len(host) > 253
            or not HOST.fullmatch(host)
            or host == "localhost"
            or host.endswith(".localhost")
        ):
            raise PluginError(
                "config_invalid: allowed_hosts: each host is a lowercase DNS name (no IP, no port)"
            )
    return frozenset(value)
