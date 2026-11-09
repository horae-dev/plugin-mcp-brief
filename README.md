<p align="center"><img src="https://raw.githubusercontent.com/horae-dev/.github/main/brand/horae-logo-500.png" width="96" alt="Horae logo"></p>

# plugin-mcp-brief

Horae plugin: call a brief() tool on any MCP service and turn its section into tasks and alerts.

> 🚧 **In progress.** The source `mcp-brief.collect` is in. Follow progress on the [Horae roadmap project](https://github.com/orgs/horae-dev/projects/1).

It declares every secret and intent it uses in `plugin.yaml`, and it only returns **intents**: the Horae core validates and applies them. It never writes anything, and a service never writes to Horae.

## `mcp-brief.collect`

Calls `brief(schema_version=1)` on each configured MCP service over streamable HTTP, validates the answer with the
[contract](#the-brief-contract) and maps it as [docs/brief-contract.md](docs/brief-contract.md) says:

| From the brief | Intent |
|---|---|
| name, headline, status, non-`crit` alerts, links | `note.replace_owned_block`, block `brief-<id>`: one section per service, rewritten on every run (the core fills the note `path` from the `act` entry) |
| each task (at most `max_tasks_per_service`) | `task.create`, fingerprint `<id>:<task id>`, `due`, and `link` if it is under a `link_prefix` |
| each `crit` alert | `notify.push`, `key` `<id>:<alert key>`, body = the alert title (escaped), at most `max_pushes_per_service` per service, `link` if it is under a `link_prefix` |

`<id>` is the `id` **you** configure, not the one in the document: a service chooses neither a block, a key nor a
host.

```yaml
- collect:
    - mcp-brief.collect:
        services:
          - id: finance                        # lowercase slug, 32 max, unique
            url: https://finance.example.org/mcp
            token_secret: MCP_BRIEF_TOKEN_1    # optional: bearer token
            timeout: 10                        # seconds, 1 to 60, default 10
            link_prefix:                       # optional
              - https://finance.example.org/app/
          - id: health
            url: https://health.example.org/mcp
        max_response_bytes: 65536              # optional, 1024 to 1048576, default 64 KiB
        max_skew_seconds: 300                  # optional, 0 to 3600, default 5 minutes
        max_pushes_per_service: 3              # optional, 1 to 10, default 3
        max_tasks_per_service: 10              # optional, 1 to 10, default 10
        max_validity_hours: 48                 # optional, 1 to 168, default 48
        allowed_hosts: []                      # only for a core older than horae-sdk 0.5
```

| Parameter | Default | Meaning |
|---|---|---|
| `services` | required | 1 to 8 services. |
| `services[].id` | required | Lowercase slug (`^[a-z0-9][a-z0-9-]{0,31}$`), unique. Names the note block (`brief-<id>`) and prefixes every task fingerprint and push key. |
| `services[].url` | required | The service's MCP endpoint: `https` only, a lowercase DNS name (no IP address, no `localhost`), no user info, no query, no fragment (a secret does not belong in a URL). |
| `services[].token_secret` | none | Name of an optional secret (`MCP_BRIEF_` + `A-Z 0-9 _`) holding a bearer token, sent as `Authorization: Bearer ...` to this service only. The shipped manifest declares `MCP_BRIEF_TOKEN_1` to `MCP_BRIEF_TOKEN_8`; to use another name, add it to `permissions.optional_secrets` of your installed `plugin.yaml` and approve it in `horae.lock`. |
| `services[].timeout` | `10` | **Total** deadline in seconds for connecting, the handshake and the call. |
| `services[].link_prefix` | none | `https` URLs ending in `/`. A task or alert link is kept only under one of them (same host, path prefix, no `..`); otherwise the item is kept without its link, and the section shows the label without a URL. List here what the `link_prefix` of your `tasks` and `notify` entries already trust, or the core would drop the link. |
| `max_response_bytes` | `65536` | Cap on the bytes of each HTTP response, enforced while they arrive: a larger body is cut off and never parsed. A service that returns its document both as structured content and as text sends it twice. |
| `max_skew_seconds` | `300` | A `generated_at` more than this far ahead of the clock is refused (`clock_skew`). |
| `max_pushes_per_service` | `3` | At most this many `notify.push` per service per run (`crit` alerts, in document order). Dedupe rests on keys the service chooses, so this bounds a service that rotates them. The rest are dropped and counted in `dropped_pushes`. |
| `max_tasks_per_service` | `10` | The same for `task.create` (task ids are chosen by the service); the surplus is counted in `dropped_tasks`. |
| `max_validity_hours` | `48` | A `valid_until` more than this many hours after `generated_at` makes the brief invalid (`schema_violation`): a brief cannot stay fresh forever. Invalid rather than stale, as the lifetime is a property of the document (like a repeated key), not an expiry, and "expired at ..." would be false. |
| `allowed_hosts` | none | See [Hosts](#hosts). |

### Output

```json
{"complete": true,
 "services": [{"id": "finance", "status": "ok", "reason": null, "brief_state": "ok",
               "tasks": 1, "pushes": 0, "dropped_tasks": 0, "dropped_pushes": 0}]}
```

`status` is `ok` or `degraded`; `reason` is a fixed code (below) when degraded; `brief_state` is the `status.state`
the service reported (`null` when no brief was read). `complete` is true when every service is `ok`. The data holds
no text from a service.

### Failures: degraded, never failed

A service that cannot be used is **degraded** with a short fixed reason. The others still flow, and **the call
returns normally**: it never fails the step. A pipeline that must react can read `complete` and `reason`. The
degraded service's section is replaced with `**Unavailable:** <reason>` so an old brief is not shown as current;
it proposes no task and no push. The response body, headers and the token are never put in a reason, an intent or a log.

| Reason | When |
|---|---|
| `config_invalid` | the service entry does not validate (not https, an IP address, a query, a bad timeout, an unknown field...) |
| `host_not_approved` | the host is not approved in `horae.lock`; nothing is sent |
| `secret_missing`, `secret_invalid` | `token_secret` is not set, is empty, or is not a printable token; nothing is sent |
| `timeout` | the total deadline passed |
| `connection_failed` | DNS, TCP or TLS failed |
| `auth_failed` | the service answered 401 or 403 |
| `http_error` | any other HTTP error status |
| `redirect_refused` | a redirect to another origin (it is never followed, so a token never travels there) |
| `oversize` | a response over `max_response_bytes` |
| `protocol_error`, `request_failed` | not a valid MCP answer, a compressed body, or an unexpected error |
| `tool_error` | the `brief` tool reported an error |
| `invalid_json` | the tool answered text that is not JSON |
| `schema_violation` | the document breaks the contract, repeats an alert `key` or task `id`, or has a `valid_until` more than `max_validity_hours` after `generated_at` |
| `clock_skew` | `generated_at` is more than `max_skew_seconds` in the future |
| `stale` | now is after `valid_until`: the section says so and shows the headline as stale; no task, no push |
| `service_error` | the service reported `status.state: error`: the section shows its reason only; its items are ignored |

A brief with `status.state: degraded` is used (the section shows the reason, alerts and tasks still count) and is
`ok` here.

### Untrusted text

Everything in a brief is untrusted. Section text is escaped with the SDK's `escape_title` (no link, wikilink,
embed, tag, Dataview, Templater or comment construct, no Tasks signifier, no control or bidi character), a block
marker is defused, and each string follows a fixed label so it can never start a heading, list, quote or checkbox.
A task title goes through `task_create`, which escapes it for Obsidian Tasks. A push title (the service name) and body (the alert title) are escaped the same way: the core and the notifier
decide how they are shown.

### Hosts

The services are yours, so `plugin.yaml` declares **no** network host. Add each host to `permissions.network` of
the installed copy and approve it in `horae.lock`:

1. Since horae-sdk 0.5 the core sends the approved list with every call and `Call.host_allowed(url)` decides: a
   service whose host is not approved is degraded with `host_not_approved` before anything is sent.
2. With a core that sends no list, the plugin falls back to its declared hosts and `allowed_hosts` (lowercase DNS
   names). It never treats the missing list as approval.

The connection ignores proxy variables from the environment and verifies TLS. A redirect is followed only within
the same scheme, host and port; anything else is `redirect_refused`.

### Install

The host starts `python3 -m mcp_brief` (`runtime.command`). Until Horae installs plugins into their own environments
(M4), pin the command to an **absolute** interpreter that has this package, as the
[SDK README](https://github.com/horae-dev/horae/blob/main/sdk/README.md#manifest) explains. It needs the network
hosts of your services and the intents `note.replace_owned_block`, `task.create` and `notify.push`. Regenerate the
manifest with `uv run python -m mcp_brief --manifest > plugin.yaml`.

The pipeline's `act` entries need a `link_prefix` for tasks and notifications if the services send links, and the
note entry needs a path. Version 0.1.0 needs a core with optional secrets (decision 56) and `notify.push` keys
(decision 57).

## The `brief()` contract

Any MCP service can feed Horae by exposing a `brief()` tool that returns a small JSON document: a headline,
alerts, tasks, links and a status. Only aggregates, never raw data; only `crit` alerts are pushed, the rest goes
through the morning note.

- Docs, field table, example service and versioning rules: [docs/brief-contract.md](docs/brief-contract.md)
- Schema (v1): [schemas/brief/v1.json](schemas/brief/v1.json), with
  [valid and invalid examples](schemas/brief/examples)
- Python model: `mcp_brief.Brief` (pydantic); the schema is generated from it with
  `uv run python scripts/export_schema.py`

## Development

```
uv sync
uv run ruff check && uv run ruff format --check
uv run mypy
uv run pytest --cov
```

## Planned work

| Milestone | Epic |
|---|---|
| M6 Existing MCP services | **MCP brief bridge** — A brief() contract and a source that calls it on any MCP service. |

Stories, estimates and acceptance criteria are in this repository's [issues](../../issues) and on the [roadmap project](https://github.com/orgs/horae-dev/projects/1).

## Contributing

This project is developed mostly by AI coding agents supervised by a human maintainer. Read [AGENTS.md](AGENTS.md) for the workflow (it applies to humans too), and the organization's [contributing guide](https://github.com/horae-dev/.github/blob/main/CONTRIBUTING.md) and [security policy](https://github.com/horae-dev/.github/blob/main/SECURITY.md).

## License

[Apache-2.0](LICENSE)
