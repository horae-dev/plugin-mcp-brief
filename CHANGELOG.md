# Changelog

Every change to tools, parameters and permissions. A new permission is called out: users
must approve it.

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
