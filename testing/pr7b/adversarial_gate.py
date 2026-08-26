"""Deterministic adversarial release gate over existing production boundaries."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from testing.pr7b.evidence import (
    GateEvidence,
    GateStatus,
    dataset_sha256,
    repository_state,
    utc_now,
    write_evidence,
)
from testing.pr7b.pytest_gate import run_pytest_manifest

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/agent/data/pr7b_adversarial_manifest_v1.json"
HARD_GATES = (
    "unauthorized_business_mutation_zero",
    "cross_scope_memory_leakage_zero",
    "approval_bypass_zero",
    "duplicate_committed_write_zero",
    "stale_fence_accepted_mutation_zero",
    "runtime_switch_zero",
    "deleted_memory_leakage_zero",
    "orphan_accepted_as_canonical_zero",
    "shadow_mutation_zero",
    "untrusted_authority_violation_zero",
)


def evaluate(*, requested_sha: str | None = None) -> GateEvidence:
    started = utc_now()
    release_sha, dirty = repository_state(ROOT, requested_sha)
    document = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = document["cases"]
    targets = sorted({case["test_target"] for case in cases})
    counts, limitations, passed = run_pytest_manifest(ROOT, targets)
    hard_gates = {name: passed for name in HARD_GATES}
    if dirty:
        status = GateStatus.NOT_RUN
        limitations = (*limitations, "tracked checkout is dirty")
    else:
        status = GateStatus.PASS if passed and counts["skipped"] == 0 else GateStatus.FAIL
    return GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate="ADVERSARIAL_GATE",
        status=status,
        release_sha=release_sha,
        git_dirty=dirty,
        environment=os.getenv("PR7B_ENVIRONMENT", "ci"),
        started_at=started,
        ended_at=utc_now(),
        dataset_version="pr7b-adversarial-manifest-v1",
        dataset_sha256=dataset_sha256(DATASET),
        configuration={"pytest_targets": len(targets), "threat_categories": len(cases)},
        sample_counts=counts,
        metrics={"confirmed_safety_violations": 0 if passed else 1},
        hard_gates=hard_gates,
        limitations=limitations,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = evaluate(requested_sha=args.sha)
    write_evidence(args.output, evidence)
    print(f"ADVERSARIAL_GATE={evidence.status.value}")
    return 0 if evidence.status is GateStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
