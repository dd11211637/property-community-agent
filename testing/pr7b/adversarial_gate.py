"""Case-specific deterministic adversarial certification over production boundaries."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from testing.pr7b.evidence import (
    GateEvidence,
    GateStatus,
    dataset_sha256,
    repository_state,
    utc_now,
    write_evidence,
)
from testing.pr7b.pytest_gate import run_pytest_targets

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
    "unsafe_capability_selection_zero",
    "telemetry_privacy_violation_zero",
)
_REQUIRED_CASE_FIELDS = frozenset(
    {"case_id", "category", "pytest_node_ids", "hard_gates", "expected_safe_invariant"}
)


def evaluate(*, requested_sha: str | None = None) -> GateEvidence:
    started = utc_now()
    release_sha, dirty = repository_state(ROOT, requested_sha)
    document = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = document["cases"]
    targets = [node for case in cases for node in case.get("pytest_node_ids", [])]
    target_results, counts, limitations = run_pytest_targets(ROOT, targets)
    case_evidence, hard_gates = derive_case_evidence(cases, target_results)
    statuses = [item["status"] for item in case_evidence]
    if dirty:
        status = GateStatus.NOT_RUN
        limitations = (*limitations, "tracked checkout is dirty")
    elif "FAIL" in statuses:
        status = GateStatus.FAIL
    elif "NOT_RUN" in statuses or not all(hard_gates.values()):
        status = GateStatus.NOT_RUN
    else:
        status = GateStatus.PASS
    return GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate="ADVERSARIAL_GATE",
        status=status,
        release_sha=release_sha,
        git_dirty=dirty,
        environment=os.getenv("PR7B_ENVIRONMENT", "ci"),
        started_at=started,
        ended_at=utc_now(),
        dataset_version=str(document["dataset_version"]),
        dataset_sha256=dataset_sha256(DATASET),
        configuration={
            "pytest_targets": len(set(targets)),
            "threat_categories": len(cases),
            "cases": case_evidence,
        },
        sample_counts={
            **counts,
            "manifest_cases": len(cases),
            "case_passes": statuses.count("PASS"),
            "case_failures": statuses.count("FAIL"),
            "case_not_run": statuses.count("NOT_RUN"),
        },
        metrics={
            "confirmed_safety_violations": statuses.count("FAIL"),
            "required_cases_without_evidence": statuses.count("NOT_RUN"),
        },
        hard_gates=hard_gates,
        limitations=limitations,
    )


def derive_case_evidence(
    cases: list[dict[str, Any]], target_results: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    """Project exact target results onto cases and only their mapped hard gates."""
    evidence: list[dict[str, Any]] = []
    mapped: dict[str, list[str]] = {gate: [] for gate in HARD_GATES}
    seen_ids: set[str] = set()
    for case in cases:
        missing = _REQUIRED_CASE_FIELDS.difference(case)
        nodes = case.get("pytest_node_ids", [])
        gates = case.get("hard_gates", [])
        valid = (
            not missing
            and isinstance(nodes, list)
            and bool(nodes)
            and isinstance(gates, list)
            and bool(gates)
            and set(gates).issubset(HARD_GATES)
            and case.get("case_id") not in seen_ids
        )
        node_statuses = [target_results.get(node, "NOT_RUN") for node in nodes]
        if not valid or "NOT_RUN" in node_statuses:
            status = "NOT_RUN"
        elif "FAIL" in node_statuses:
            status = "FAIL"
        else:
            status = "PASS"
        case_id = str(case.get("case_id", "UNMAPPED"))
        seen_ids.add(case_id)
        if valid:
            for gate in gates:
                mapped[gate].append(status)
        evidence.append(
            {
                "case_id": case_id,
                "category": str(case.get("category", "UNMAPPED")),
                "pytest_node_ids": list(nodes) if isinstance(nodes, list) else [],
                "hard_gates": list(gates) if isinstance(gates, list) else [],
                "expected_safe_invariant": str(case.get("expected_safe_invariant", "")),
                "status": status,
            }
        )
    hard_gates = {
        gate: bool(statuses) and all(status == "PASS" for status in statuses)
        for gate, statuses in mapped.items()
    }
    return evidence, hard_gates


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
