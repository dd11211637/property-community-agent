"""Export the unified application OpenAPI contract for review and integration."""

from __future__ import annotations

import json
from pathlib import Path

from property_agent.main import app


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Exported {target}")


if __name__ == "__main__":
    main()
