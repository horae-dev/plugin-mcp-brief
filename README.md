<p align="center"><img src="https://raw.githubusercontent.com/horae-dev/.github/main/brand/horae-logo-500.png" width="96" alt="Horae logo"></p>

# plugin-mcp-brief

Horae plugin: call a brief() tool on any MCP service and turn its section into tasks and alerts.

> 🚧 **Planned.** The plugin itself is not built yet (story #3), but the `brief()` contract it implements is published: see below. Follow progress on the [Horae roadmap project](https://github.com/orgs/horae-dev/projects/1).

## What this plugin will do

Horae plugin: call a brief() tool on any MCP service and turn its section into tasks and alerts.

It will declare every host, secret, path and intent it uses in `plugin.yaml`, and it will only return **intents**: the Horae core validates and applies them.

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
