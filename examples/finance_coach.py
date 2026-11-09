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
