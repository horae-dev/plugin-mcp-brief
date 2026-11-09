# Changelog

Every change to tools, parameters and permissions. A new permission is called out: users
must approve it.

## 0.1.1

- **Hardening** (#7). New optional parameters of `mcp-brief.collect`; no new permission.
  - A `notify.push` title (`service.name`) and body (`alert.title`) are escaped like note text.
  - `max_pushes_per_service` (1 to 10, default 3) and `max_tasks_per_service` (1 to 10,
    default 10) cap what one service adds per run; the first ones in document order are kept.
    Each result row gains `dropped_pushes` and `dropped_tasks` (counts only).
  - `max_validity_hours` (1 to 168, default 48): a `valid_until` further than this after
    `generated_at` makes the brief invalid (`schema_violation`).
  - The `mcp` client's `Mcp-Session-Id` log line (logger `mcp.client.streamable_http`) is lowered
    to WARNING when the plugin starts.

## 0.1.0

- **New tool `mcp-brief.collect`** (#3): calls `brief(schema_version=1)` on each configured MCP
  service over streamable HTTP and returns, per service, a note section
  (`note.replace_owned_block`, block `brief-<id>`), a `task.create` per task (fingerprint
  `<id>:<task id>`) and a `notify.push` per `crit` alert (key `<id>:<alert key>`), as
  `docs/brief-contract.md` describes. Parameters: `services` (1 to 8 of `id`, `url`,
  `token_secret`, `timeout`, `link_prefix`), `max_response_bytes` (default 64 KiB),
  `max_skew_seconds` (default 300) and `allowed_hosts`.
- A service that times out, cannot be reached, refuses the token (401/403), answers garbage, a
  document that breaks the contract, an oversized or a stale one is **degraded** with a short
  fixed reason; the other services still flow and the step never fails. The body and the token
  are never echoed.
- Hosts are checked with `Call.host_allowed` (horae-sdk 0.5): an unapproved host is degraded
  with `host_not_approved` before anything is sent. A redirect to another host is refused.
- **New permissions:** intents `note.replace_owned_block`, `task.create` and `notify.push`;
  optional secrets `MCP_BRIEF_TOKEN_1` to `MCP_BRIEF_TOKEN_8` (a bearer token per service).
  No network host is declared: add the hosts of your services to `permissions.network` and
  approve them in `horae.lock`.
- Requires horae-sdk 0.5 and a core with optional secrets (decision 56) and `notify.push` keys
  (decision 57).
