"""Write schemas/brief/v1.json from the pydantic model: `uv run python scripts/export_schema.py`."""

import json
from pathlib import Path

from mcp_brief import brief_json_schema

TARGET = Path(__file__).resolve().parent.parent / "schemas" / "brief" / "v1.json"

if __name__ == "__main__":
    TARGET.write_text(json.dumps(brief_json_schema(), indent=2, ensure_ascii=False) + "\n")
