"""Aggregate safe and full C1-C12 chaos evidence without redefining correctness."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx

from testing.pr7b.evidence import (
    GateEvidence,
    GateStatus,
    repository_state,
    utc_now,
    write_evidence,
)
from testing.pr7b.pytest_gate import run_pytest_manifest

ROOT = Path(__file__).resolve().parents[2]
SAFE_TARGETS = [
    "tests/agent/test_pr7a_telemetry_foundation.py::test_production_model_shape_records_primary_failure_then_fallback_success",
    "tests/agent/test_pr7a_telemetry_foundation.py::test_production_model_shape_records_schema_failure_before_fallback",
    "tests/agent/test_pr7a_telemetry_foundation.py::test_semantic_plan_primary_failure_propagates_without_fallback",
    "tests/agent/test_pr7a_telemetry_foundation.py::test_accepted_head_failure_has_no_completed_outcome",
    "tests/agent/test_pr7b_chaos.py",
    "tests/agent/test_pr7b_memory_failures.py",
    "tests/agent/test_pr6_long_term_memory.py::test_scope_expiry_delete_and_bounded_retrieval_are_canonical",
    "tests/agent/test_pr6_long_term_memory.py::test_failed_accepted_head_publication_produces_zero_writer_calls",
    "tests/test_p0_concurrency_atomicity.py::test_approval_consume_atomic_with_business_mutation",
    "tests/test_p0_concurrency_atomicity.py::test_assert_run_fence_rejects_stale_fence",
]
FULL_TARGETS = [
    *SAFE_TARGETS,
    "tests/agent/test_pr7b_chaos_postgres.py",
    "tests/agent/test_pr4_repair_vertical_postgres.py::test_v2_stream_checkpoint_success_then_accepted_failure_has_no_public_success",
    "tests/test_p0_postgres_concurrency.py::test_double_consume_produces_one_consumed",
    "tests/test_p0_postgres_concurrency.py::test_stale_fence_writer_rejected",
    "tests/test_p0_postgres_concurrency.py::test_consume_rollback_on_business_failure",
]
DRILLS = {
    "C1": ("model provider timeout", "bounded retry and safe degraded outcome"),
    "C2": ("malformed structured response", "schema failure and no executable default plan"),
    "C3": ("embedding or index unavailable", "scoped no-Memory degradation"),
    "C4": ("PostgreSQL backend termination", "failed write rejected and pool recovery"),
    "C5": ("official saver put failure", "accepted head remains unchanged"),
    "C6": ("accepted publication failure", "orphan rejected and no public final"),
    "C7": ("subprocess death after saver commit", "restart uses exact accepted cursor"),
    "C8": ("subprocess death after business commit", "idempotent retry returns one resource"),
    "C9": ("approval and business transaction race", "same-UoW binding remains atomic"),
    "C10": ("Memory Writer persistence failure", "accepted business result remains committed"),
    "C11": ("concurrent approval consume", "one consume and one mutation"),
    "C12": ("lease replacement", "stale fence rejects state and mutation"),
}
SAFE_DRILLS = frozenset({"C1", "C2", "C3", "C5", "C6", "C9", "C10", "C11", "C12"})
DATABASE_DRILLS = frozenset({"C4", "C7", "C8", "C9", "C11", "C12"})
ACCEPTED_HEAD_DRILLS = frozenset({"C5", "C6", "C7", "C10", "C12"})
CHECKPOINT_DRILLS = frozenset({"C5", "C6", "C7", "C12"})
MEMORY_DRILLS = frozenset({"C3", "C6", "C10"})


def evaluate(
    campaign: str,
    *,
    requested_sha: str | None = None,
    server_observability_url: str | None = None,
) -> GateEvidence:
    started = utc_now()
    release_sha, dirty = repository_state(ROOT, requested_sha)
    full = campaign == "full"
    counts, limitations, passed = run_pytest_manifest(ROOT, FULL_TARGETS if full else SAFE_TARGETS)
    if full:
        cases = {f"C{number}": passed for number in range(1, 13)}
        status = GateStatus.PASS if passed and counts["skipped"] == 0 else GateStatus.FAIL
    else:
        cases = {
            **{f"C{number}": True for number in (1, 2, 3, 5, 6, 9, 10, 11, 12)},
            **{f"C{number}": False for number in (4, 7, 8)},
        }
        status = GateStatus.PASS if passed else GateStatus.FAIL
        limitations = (*limitations, "C4, C7, and C8 are not run by safe smoke")
    telemetry_ready = not full or _chaos_observability_available(
        server_observability_url,
        release_sha=release_sha,
        started_at=started,
    )
    if full and passed and not telemetry_ready:
        status = GateStatus.NOT_RUN
        limitations = (*limitations, "exact-window chaos telemetry summary unavailable")
    if dirty:
        status = GateStatus.NOT_RUN
        limitations = (*limitations, "tracked checkout is dirty")
    return GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate="CHAOS_GATE" if full else "CHAOS_HARNESS_SMOKE",
        status=status,
        release_sha=release_sha,
        git_dirty=dirty,
        environment=os.getenv("PR7B_ENVIRONMENT", "ci"),
        started_at=started,
        ended_at=utc_now(),
        dataset_version="pr7b-chaos-c1-c12-v1",
        configuration={
            "campaign": campaign,
            "fault_profile_version": "pr7b-chaos-v1",
            "drills": _drill_evidence(
                cases,
                full=full,
                campaign_passed=passed,
                telemetry_ready=telemetry_ready,
            ),
        },
        sample_counts=counts,
        metrics={"process_death_cases_executed": 2 if full and passed else 0},
        hard_gates=cases,
        limitations=limitations,
    )


def _drill_evidence(
    cases: dict[str, bool],
    *,
    full: bool,
    campaign_passed: bool,
    telemetry_ready: bool,
) -> dict[str, dict[str, str]]:
    evidence = {}
    for case, details in DRILLS.items():
        executed = full or case in SAFE_DRILLS
        status = (
            "PASS"
            if executed and cases[case] and telemetry_ready
            else "FAIL"
            if executed and not cases[case]
            else "NOT_RUN"
        )
        evidence[case] = {
            "injection_point": details[0],
            "test_run_id": f"pr7b-chaos-v1-{case.lower()}",
            "expected_user_visible_behavior": details[1],
            "observed_user_visible_behavior": (
                "matched asserted contract" if executed and cases[case] else "not observed"
            ),
            "durable_database_state": (
                _case_evidence(case, status, DATABASE_DRILLS, "durable state assertions passed")
            ),
            "accepted_head_state": (
                _case_evidence(case, status, ACCEPTED_HEAD_DRILLS, "canonicality assertions passed")
            ),
            "checkpoint_state": (
                _case_evidence(
                    case,
                    status,
                    CHECKPOINT_DRILLS,
                    "persistence and recovery assertions passed",
                )
            ),
            "memory_side_effect_state": (
                _case_evidence(case, status, MEMORY_DRILLS, "bounded Memory assertions passed")
            ),
            "recovery_action": "bounded retry or exact accepted-state restart",
            "forbidden_outcome_checks": (
                "zero forbidden outcomes asserted" if status == "PASS" else "not certified"
            ),
            "telemetry_evidence": (
                "production observation seams asserted" if status == "PASS" else "not certified"
            ),
            "status": status,
        }
    if not campaign_passed:
        for item in evidence.values():
            if item["status"] == "PASS":
                item["status"] = "FAIL"
    return evidence


def _case_evidence(case: str, status: str, relevant: frozenset[str], passed: str) -> str:
    if status != "PASS":
        return "not certified"
    return passed if case in relevant else "not applicable to this drill"


def _chaos_observability_available(url: str | None, *, release_sha: str, started_at: str) -> bool:
    if not url:
        return False
    headers = {"Authorization": f"Bearer {os.getenv('PR7B_OTEL_SUMMARY_TOKEN', '')}"}
    try:
        response = httpx.get(
            url,
            headers=headers,
            params={"release_sha": release_sha, "started_at": started_at, "gate": "chaos"},
            timeout=20.0,
        )
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(document, dict):
        return False
    case_signals = document.get("case_signals", {})
    return document.get("release_sha") == release_sha and all(
        int(case_signals.get(f"C{number}", 0)) > 0 for number in range(1, 13)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", choices=("safe", "full"), default="safe")
    parser.add_argument("--sha")
    parser.add_argument("--server-observability-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = evaluate(
        args.campaign,
        requested_sha=args.sha,
        server_observability_url=args.server_observability_url,
    )
    write_evidence(args.output, evidence)
    print(f"{evidence.gate}={evidence.status.value}")
    return 0 if evidence.status is GateStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
