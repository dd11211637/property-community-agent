"""Enforce the selected protected certification result after artifacts are built."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SELECTION_GATES = {
    "all": (
        "REAL_MODEL_GATE",
        "MEMORY_GATE",
        "LOAD_GATE",
        "CHAOS_GATE",
        "ADVERSARIAL_GATE",
    ),
    "real-model": ("REAL_MODEL_GATE",),
    "memory": ("MEMORY_GATE",),
    "load": ("LOAD_GATE",),
    "chaos": ("CHAOS_GATE",),
}


def selected_statuses(document: dict[str, Any], selection: str) -> dict[str, str]:
    """Return explicit statuses for every gate selected by the workflow dispatch."""
    if selection not in SELECTION_GATES:
        raise ValueError(f"unsupported certification selection: {selection}")
    gates = document.get("gates", {})
    if not isinstance(gates, dict):
        gates = {}
    return {
        gate: str(gates.get(gate, {}).get("status", "NOT_RUN"))
        for gate in SELECTION_GATES[selection]
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--gate", choices=tuple(SELECTION_GATES), required=True)
    args = parser.parse_args()
    try:
        document = json.loads(args.baseline.read_text(encoding="utf-8"))
        statuses = selected_statuses(document, args.gate)
    except (OSError, ValueError, TypeError) as exc:
        print(f"PR7B_CERTIFICATION=FAIL ({type(exc).__name__})")
        return 1
    for gate, status in statuses.items():
        print(f"{gate}={status}")
    passed = all(status == "PASS" for status in statuses.values())
    print(f"PR7B_CERTIFICATION={'PASS' if passed else 'NOT_PASS'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
