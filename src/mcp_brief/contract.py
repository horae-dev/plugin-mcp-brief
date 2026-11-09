"""The brief() contract, version 1.

`Brief` is the source of truth. `schemas/brief/v1.json` is generated from it
(`uv run python scripts/export_schema.py`) and a test fails when they differ.
Every text field is untrusted: Horae renders it as data (see docs/brief-contract.md).
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
SCHEMA_ID = (
    "https://raw.githubusercontent.com/horae-dev/plugin-mcp-brief/main/schemas/brief/v1.json"
)

# One line of text: no control (Cc), format (Cf, bidi and zero-width included) or line and
# paragraph separator (Zl, Zp) character. The class lists the BMP ones; `_Strict` checks the rest.
_LINE = (
    r"^[^\x00-\x1f\x7f-\x9f\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]+$"
)
_KEY = r"^[A-Za-z0-9._:-]{1,64}$"  # same alphabet as a Horae fingerprint, shorter
_STAMP = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$"
)
# https, an ASCII host (punycode for IDN), no user info, printable ASCII after it (percent-encode
# anything else), no backslash.
_URL = (
    r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?"
    r"(?:[/?#][\x21-\x5b\x5d-\x7e]*)?$"
)

Line = Annotated[str, Field(min_length=1, pattern=_LINE)]
Key = Annotated[str, Field(pattern=_KEY)]
Url = Annotated[str, Field(min_length=9, max_length=2048, pattern=_URL)]
Stamp = Annotated[
    str,
    Field(min_length=20, max_length=32, pattern=_STAMP, json_schema_extra={"format": "date-time"}),
]


_REFUSED = frozenset({"Cc", "Cf", "Zl", "Zp"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def _no_invisible_characters(cls, value: object) -> object:
        # Also covers astral format characters (tag characters, for one) that no BMP class can.
        if isinstance(value, str) and any(unicodedata.category(c) in _REFUSED for c in value):
            raise ValueError("control, format and separator characters are not allowed")
        return value


class Service(_Strict):
    """Who produced this brief."""

    id: Annotated[
        str,
        Field(
            pattern=r"^[a-z0-9][a-z0-9-]{0,31}$",
            description="Stable lowercase slug. Namespaces every key and id below.",
        ),
    ]
    name: Annotated[
        str, Field(min_length=1, max_length=60, pattern=_LINE, description="Section title.")
    ]


class Status(_Strict):
    """Can the service stand behind this brief?"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {
                "properties": {"state": {"enum": ["degraded", "error"]}},
                "required": ["state"],
            },
            "then": {"properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        },
    )

    state: Literal["ok", "degraded", "error"]
    reason: Annotated[
        str | None,
        Field(
            default=None,
            max_length=200,
            pattern=_LINE,
            description="Why the state is not `ok`. Required unless the state is `ok`.",
        ),
    ]

    @model_validator(mode="after")
    def _reason_when_not_ok(self) -> Status:
        if self.state != "ok" and self.reason is None:
            raise ValueError("status.reason is required unless the state is ok")
        return self


class Alert(_Strict):
    """Something the user should know. Only `crit` is pushed; the rest ride the brief."""

    key: Annotated[
        str,
        Field(
            pattern=_KEY,
            description="Stable per condition (not per occurrence). Horae dedupes on it.",
        ),
    ]
    severity: Literal["info", "warn", "crit"]
    title: Annotated[str, Field(min_length=1, max_length=120, pattern=_LINE)]
    body: Annotated[str | None, Field(default=None, max_length=500, pattern=_LINE)]
    link: Annotated[Url | None, Field(default=None)]


class Task(_Strict):
    """Something the user should do."""

    id: Annotated[
        str,
        Field(
            pattern=_KEY,
            description="Stable per task. Horae derives the task fingerprint and block id.",
        ),
    ]
    title: Annotated[str, Field(min_length=1, max_length=200, pattern=_LINE)]
    due: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
            description="Due date, `YYYY-MM-DD`.",
            json_schema_extra={"format": "date"},
        ),
    ]
    link: Annotated[Url | None, Field(default=None)]

    @field_validator("due")
    @classmethod
    def _real_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)  # the schema pattern cannot tell 2026-02-31 from a date
        return value


class Link(_Strict):
    """A place to read more."""

    label: Annotated[str, Field(min_length=1, max_length=80, pattern=_LINE)]
    url: Url


class Brief(_Strict):
    """What a service returns from its `brief()` tool (contract v1)."""

    model_config = ConfigDict(extra="forbid", title="Horae brief v1")

    schema_version: Annotated[Literal[1], Field(description="Major version of this contract.")]
    service: Service
    generated_at: Annotated[Stamp, Field(description="RFC 3339, with an offset.")]
    valid_until: Annotated[
        Stamp, Field(description="After this instant the brief is stale and is not trusted.")
    ]
    status: Status
    headline: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            pattern=_LINE,
            description="One-line summary: the morning-note section's first line.",
        ),
    ]
    alerts: Annotated[list[Alert], Field(default_factory=list, max_length=10)]
    tasks: Annotated[list[Task], Field(default_factory=list, max_length=10)]
    links: Annotated[list[Link], Field(default_factory=list, max_length=5)]

    @field_validator("generated_at", "valid_until")
    @classmethod
    def _real_instant(cls, value: str) -> str:
        datetime.fromisoformat(value)  # rejects month 13, hour 25, and so on
        return value

    @model_validator(mode="after")
    def _window(self) -> Brief:
        if datetime.fromisoformat(self.valid_until) <= datetime.fromisoformat(self.generated_at):
            raise ValueError("valid_until must be after generated_at")
        return self


def brief_json_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of `Brief`, as published in schemas/brief/v1.json."""
    schema = Brief.model_json_schema(mode="validation")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_ID, **schema}
