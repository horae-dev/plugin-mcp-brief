# The `brief()` contract (v1)

A service that wants to feed [Horae](https://github.com/horae-dev/horae) exposes one
[MCP](https://modelcontextprotocol.io) tool named `brief`. It returns one JSON document: a headline, alerts,
tasks, links and a status. [`plugin-mcp-brief`](../README.md) calls the tool and turns the document into a
section of the morning note, tasks and alerts. The service never writes to Horae, and Horae never reads the
service's raw data.

- Schema: [`schemas/brief/v1.json`](../schemas/brief/v1.json)
  (`$id` `https://raw.githubusercontent.com/horae-dev/plugin-mcp-brief/main/schemas/brief/v1.json`,
  JSON Schema draft 2020-12). It is generated from the pydantic model in
  [`src/mcp_brief/contract.py`](../src/mcp_brief/contract.py); a test fails if the two differ.
- Examples: [`schemas/brief/examples/valid`](../schemas/brief/examples/valid) and
  [`invalid`](../schemas/brief/examples/invalid), every one checked in CI.

## Principles

1. **Aggregates only.** Send "groceries at 82% of budget", never the transactions behind it. Send "load is
   moderate", never the heart-rate stream. The schema has no free-form field and no way to attach data:
   `additionalProperties` is `false` everywhere.
2. **One channel for everything but emergencies.** Only a `crit` alert is pushed directly. Everything else
   (`info` and `warn` alerts, tasks, the headline) reaches the user through the morning note. A service must
   stop sending its own day-to-day notifications; otherwise the user is notified twice.
3. **Stable ids.** Alerts and tasks carry a key or id that does not change between calls for the same
   condition or task. Horae dedupes on it.
4. **Honest freshness.** The document says when it was made, until when it can be trusted, and whether the
   service stands behind it.

## Calling the tool

`brief(schema_version: int = 1) -> object`

`schema_version` is the highest major version Horae understands. A service answers with a document whose own
`schema_version` is at most that value, or fails with a tool error if it cannot. Horae refuses a document with
a version it does not know. The tool takes no other argument: what to include is the service's decision, and it
must be safe to call at any time (read-only, idempotent, fast).

## Fields

Limits are enforced by the schema. "One line" means no control (Cc), format (Cf, which includes bidi overrides and zero-width characters) or line or paragraph separator (Zl, Zp) character, so no newline, tab, C1 control, U+202E or U+200B. URLs are `https://` with an ASCII host (punycode for international names), no user info, and printable ASCII after it (percent-encode anything else).

| Field | Type | Req. | Limits | Semantics |
|---|---|---|---|---|
| `schema_version` | integer | yes | `1` | Major version of this contract. |
| `service.id` | string | yes | `^[a-z0-9][a-z0-9-]{0,31}$` | Stable slug. Namespaces every key and id, and names the owned note block. |
| `service.name` | string | yes | 1-60, one line | Title of the morning-note section. |
| `generated_at` | string | yes | RFC 3339 with offset | When the service computed this brief. |
| `valid_until` | string | yes | RFC 3339 with offset | After this instant the brief is stale. |
| `status.state` | enum | yes | `ok`, `degraded`, `error` | `ok`: trust it. `degraded`: partial or estimated data, still shown. `error`: nothing usable. |
| `status.reason` | string or null | if not `ok` | max 200, one line | Why. Required when the state is `degraded` or `error`. |
| `headline` | string | yes | 1-200, one line | One-sentence summary; first line of the section. |
| `alerts` | array | no | max 10 | Things to know, see below. |
| `alerts[].key` | string | yes | `^[A-Za-z0-9._:-]{1,64}$` | Stable per condition, not per occurrence (`balance.checking.low`, not a timestamp). |
| `alerts[].severity` | enum | yes | `info`, `warn`, `crit` | `crit` is pushed; `info` and `warn` appear in the section only. |
| `alerts[].title` | string | yes | 1-120, one line | What is wrong. |
| `alerts[].body` | string | no | max 500, one line | Detail. |
| `alerts[].link` | string | no | `https://`, max 2048 | Where to look. |
| `tasks` | array | no | max 10 | Things to do, see below. |
| `tasks[].id` | string | yes | `^[A-Za-z0-9._:-]{1,64}$` | Stable per task (`bill.electricity.2026-11`). |
| `tasks[].title` | string | yes | 1-200, one line | What to do. |
| `tasks[].due` | string | no | `YYYY-MM-DD` | Due date. |
| `tasks[].link` | string | no | `https://`, max 2048 | The item the task is about. |
| `links` | array | no | max 5 | Where to read more. |
| `links[].label` | string | yes | 1-80, one line | Link text. |
| `links[].url` | string | yes | `https://`, max 2048 | Target. |

Beyond the schema, the plugin also refuses impossible dates (`2026-02-31`) and a `valid_until` that is not after `generated_at`; the pydantic model does the same.

Omit optional fields rather than sending `null`, except `status.reason`, which may be `null` when `ok`.
Horae also refuses a response larger than 64 KiB, before parsing it (enforced by the plugin, not the schema; the cap and the 5-minute `generated_at` tolerance below are configurable per pipeline, see the [README](../README.md#mcp-briefcollect)).

## How Horae maps a brief onto its intents

The plugin only returns [intents](https://github.com/horae-dev/horae/tree/main/schemas/intents); the core
validates, routes and applies them. This is the mapping the contract is designed for.

| Brief | Horae intent | Notes |
|---|---|---|
| `service.name`, `headline`, non-`crit` alerts, links | `note.replace_owned_block` in the morning note, block id `brief-<service.id>` | The section is rewritten on every run, so it never duplicates. A `crit` alert is listed here too, once. |
| `alerts[]` with `severity: crit` | `notify.push` with `key` = `<service.id>:<alert.key>` | One push per key per local day, however many runs see it (core decision 57). The body is the alert title; the link is sent only if it falls under the notify entry's trusted `link_prefix`. |
| `tasks[]` | `task.create` with `fingerprint` = `<service.id>:<task.id>` | Fingerprint, and from it the task's block id, is stable across runs and sources. At most 97 characters, inside the core's 128. |
| `tasks[].link` | `task.create` `link` | Routed only under the tasks entry's trusted `link_prefix` list (core decision 60). A task whose link is off-prefix is skipped, with a warning in the run record, not fatal. Ask the operator to list your host. |
| `status` | the section, and whether the others are emitted | See below. |

Rules the plugin applies:

- **Stale.** If the current time is after `valid_until`, the section says the brief is stale, shows
  `headline` only as such, and emits no task and no push. A `generated_at` more than 5 minutes in the future is
  refused.
- **`degraded`.** The section shows the reason next to the headline. Alerts and tasks are still used.
- **`error`.** The section shows the reason only. No task, no push, whatever the document holds.
- **Uniqueness.** Two alerts with the same `key`, or two tasks with the same `id`, make the whole document
  invalid.
- **Only `crit` pushes.** If the service also pushes on its own, the user gets two notifications. Remove that.

## Example service

A finance coach with the `mcp` Python SDK. The same file is
[`examples/finance_coach.py`](../examples/finance_coach.py), and CI checks that it returns a valid v1 document
and that this block is identical to it.

<!-- example-service -->
```python
"""A minimal MCP service that feeds Horae: a finance coach exposing `brief()`."""

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("finance-coach")


@mcp.tool()
def brief(schema_version: int = 1) -> dict[str, Any]:
    """Return today's brief as a Horae brief v1 document. Aggregates only, never transactions."""
    now = datetime.now(UTC).replace(microsecond=0)
    spent_pct = 82  # computed from your own data; only the aggregate leaves this process
    alerts = []
    if spent_pct >= 80:
        alerts.append(
            {
                "key": "budget.groceries.80pct",  # stable per condition, not per day
                "severity": "warn",  # only "crit" is pushed; "warn" rides the morning note
                "title": f"Groceries at {spent_pct}% of the monthly budget",
            }
        )
    return {
        "schema_version": 1,
        "service": {"id": "finance-coach", "name": "Finance"},
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "valid_until": (now + timedelta(hours=12)).isoformat().replace("+00:00", "Z"),
        "status": {"state": "ok"},
        "headline": "Spending is 8% under budget this month; one bill is due Friday.",
        "alerts": alerts,
        "tasks": [
            {
                "id": "bill.electricity.2026-11",  # stable per task: becomes the fingerprint
                "title": "Pay the electricity bill",
                "due": "2026-11-14",
            }
        ],
        "links": [{"label": "Monthly overview", "url": "https://finance.example.test/overview"}],
    }


if __name__ == "__main__":
    mcp.run()
```

Run it with `uv run python examples/finance_coach.py` (stdio transport). The document it returns is
[`finance-coach.json`](../schemas/brief/examples/valid/finance-coach.json), save for the dates.

## Versioning

- The version is the major number in `schema_version` and in the file name: `schemas/brief/v1.json`.
- **Additive changes stay in v1.** A new optional field, a new optional object, or a looser cap that no valid v1
  document could rely on. The file is updated in place, with a line in the changelog below. Because the schema
  is strict, a service must send a new field only once the Horae version it targets bundles the revision that
  has it.
- **Breaking changes go to v2**, published beside v1 at `schemas/brief/v2.json`. Breaking means: removing or
  renaming a field, making an optional field required, tightening a cap or pattern, adding a value to an enum
  (`severity`, `status.state`), or changing what a field means.
- v1 is never edited in a breaking way. Horae accepts the two most recent major versions, and passes the
  highest it knows in the `brief` tool's `schema_version` argument, so a service can serve both during a
  migration.

Changelog: **v1.0**, first release.

## Security

Everything in a brief is **untrusted input**, however friendly the service. A service can be compromised, or can
relay text that someone else wrote (a payee name, a calendar title, a coach's free text). So:

- **Horae renders brief text as data, never as instructions or markup.** Titles, headlines, reasons and bodies
  are escaped before they reach a note, a task line or a notification, and pass the core's guard (no note
  structure, no Obsidian active construct such as Dataview or Templater, no Tasks signifier, no owned-block
  marker, no control or bidi character). A service cannot create a checkbox, a task, a link or a heading by
  smuggling markup into a string.
- **Brief text never reaches an LLM as instructions.** If a pipeline passes it to a skill, it is quoted as
  data, and a skill declared `links: none` writes no link at all, even one the brief contained (core decision 55).
- **Links are not free.** Only `https://` is accepted by the schema. Even so, Horae routes a link only under a
  `link_prefix` the operator trusts, and drops the rest (core decisions 30, 52, 60). A service cannot point the
  user at a host the operator has not named.
- **Text is capped.** Counts, lengths and the whole response size are bounded, and unknown fields are refused,
  so a brief cannot carry a payload or flood a note.
- **Aggregates only.** Never put raw data, credentials, account numbers or personal identifiers in a brief.
  The brief ends up in a note that may be synced and backed up.
- **The service is not trusted with decisions.** It cannot choose a note path, an intent, a recipient or a
  host. It supplies values that Horae fits into its own intents.
