"""Build a bounded exact-SHA R0 certification summary from gate artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from testing.pr7b.capacity import R0_PROFILE_VERSION
from testing.pr7b.evidence import repository_state, utc_now, validate_safe_payload

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_GATES = (
    "REAL_MODEL_GATE",
    "MEMORY_GATE",
    "LOAD_GATE",
    "CHAOS_GATE",
    "ADVERSARIAL_GATE",
)


def build(paths: list[Path], *, requested_sha: str | None = None) -> dict[str, Any]:
    release_sha, dirty = repository_state(ROOT, requested_sha)
    artifacts = [_read_gate(path, release_sha) for path in paths]
    by_gate = {artifact["gate"]: artifact for artifact in artifacts}
    gates = {
        name: by_gate.get(name, {"gate": name, "status": "NOT_RUN"}) for name in REQUIRED_GATES
    }
    result = {
        "schema_version": "pr7b-r0-baseline-v1",
        "release_sha": release_sha,
        "git_dirty": dirty,
        "generated_at": utc_now(),
        "capacity_profile_version": R0_PROFILE_VERSION,
        "promotion_authorized": False,
        "gates": gates,
        "known_limitations": [
            "R0 is a preproduction target, not a measured production traffic peak",
            "PR7-C rollout approval is outside this artifact",
        ],
        "deferred": ["PR7-C", "PR7-D", "PR7-E", "PR7-F"],
    }
    validate_safe_payload(result)
    return result


def _read_gate(path: Path, release_sha: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("release_sha") != release_sha:
        raise ValueError(f"stale evidence SHA in {path}")
    return {
        key: document[key]
        for key in (
            "gate",
            "status",
            "dataset_version",
            "dataset_sha256",
            "configuration",
            "sample_counts",
            "metrics",
            "hard_gates",
            "limitations",
        )
        if key in document
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha")
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.evidence, requested_sha=args.sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"PR7B_R0_BASELINE={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
