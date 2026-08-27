"""Per-drill C1-C12 chaos evidence without redefining correctness ownership."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from testing.pr7b.chaos_signals import matching_signals
from testing.pr7b.evidence import (
    GateEvidence,
    GateStatus,
    repository_state,
    utc_now,
    write_evidence,
)
from testing.pr7b.pytest_gate import run_pytest_targets

ROOT = Path(__file__).resolve().parents[2]
_CAMPAIGN_ID = re.compile(r"[a-f0-9]{32}")
SAFE_DRILLS = frozenset({"C1", "C2", "C3", "C5", "C6", "C9", "C10", "C12"})
DRILL_MANIFEST: dict[str, dict[str, Any]] = {
    "C1": {
        "injection_point": "model provider timeout",
        "pytest_node_ids": [
            "tests/agent/test_pr7a_telemetry_foundation.py::test_production_model_shape_records_primary_failure_then_fallback_success",
            "tests/agent/test_pr7a_telemetry_foundation.py::test_semantic_plan_primary_failure_propagates_without_fallback",
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7a_telemetry_foundation.py::test_production_model_shape_records_primary_failure_then_fallback_success"
        ],
        "user_visible_assertion": "fallback is degraded success or forbidden fallback propagates",
        "durable_database_assertion": "not applicable; provider boundary executes before mutation",
        "accepted_head_assertion": "forbidden semantic fallback produces no accepted completion",
        "checkpoint_assertion": "not applicable to provider-attempt drill",
        "memory_assertion": "no Memory authority is introduced by fallback",
        "required_telemetry": "primary timeout and fallback/overall outcome",
    },
    "C2": {
        "injection_point": "malformed structured response",
        "pytest_node_ids": [
            "tests/agent/test_pr7a_telemetry_foundation.py::test_production_model_shape_records_schema_failure_before_fallback"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7a_telemetry_foundation.py::test_production_model_shape_records_schema_failure_before_fallback"
        ],
        "user_visible_assertion": "schema failure is distinct from deterministic fallback outcome",
        "durable_database_assertion": (
            "not applicable; invalid provider response is not a business write"
        ),
        "accepted_head_assertion": "no executable invalid provider plan is published",
        "checkpoint_assertion": "not applicable to provider schema drill",
        "memory_assertion": "not applicable",
        "required_telemetry": "primary schema failure and fallback/overall outcome",
    },
    "C3": {
        "injection_point": "embedding or vector index unavailable",
        "pytest_node_ids": [
            "tests/agent/test_pr7b_memory_failures.py::test_embedding_vector_outage_uses_only_structured_scope_safe_results"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7b_memory_failures.py::test_embedding_vector_outage_uses_only_structured_scope_safe_results"
        ],
        "user_visible_assertion": "reasoning degrades to scoped structured no-Memory input",
        "durable_database_assertion": "canonical business state is not replaced by vector fallback",
        "accepted_head_assertion": "turn may proceed only without unscoped Memory",
        "checkpoint_assertion": "not applicable",
        "memory_assertion": "no cross-scope or unstructured fallback retrieval",
        "required_telemetry": "embedding/index degradation reason and fallback mode",
    },
    "C4": {
        "injection_point": "PostgreSQL backend termination",
        "pytest_node_ids": [
            "tests/agent/test_pr7b_chaos_postgres.py::test_c4_transient_postgres_interruption_rejects_failed_transaction_and_recovers"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7b_chaos_postgres.py::test_c4_transient_postgres_interruption_rejects_failed_transaction_and_recovers"
        ],
        "user_visible_assertion": "failed transaction is rejected and readiness recovers",
        "durable_database_assertion": "injected marker row count remains zero after recovery",
        "accepted_head_assertion": "not exercised by this dependency-level interruption subcase",
        "checkpoint_assertion": "failed checkpoint insert leaves no row",
        "memory_assertion": "not applicable",
        "required_telemetry": "database failure/readiness transition and pool recovery",
    },
    "C5": {
        "injection_point": "official LangGraph saver put failure",
        "pytest_node_ids": [
            "tests/agent/test_pr7b_chaos.py::test_c5_official_checkpoint_failure_never_advances_application_accepted_head"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7b_chaos.py::test_c5_official_checkpoint_failure_never_advances_application_accepted_head"
        ],
        "user_visible_assertion": "official saver failure propagates without success",
        "durable_database_assertion": "application accepted-head store remains empty",
        "accepted_head_assertion": "accepted head does not advance",
        "checkpoint_assertion": "injected official saver put fails",
        "memory_assertion": "no accepted outcome exists for Writer input",
        "required_telemetry": "checkpoint persist failure",
    },
    "C6": {
        "injection_point": "application accepted-head publication failure",
        "pytest_node_ids": [
            "tests/agent/test_pr7a_telemetry_foundation.py::test_accepted_head_failure_has_no_completed_outcome",
            "tests/agent/test_pr6_long_term_memory.py::test_failed_accepted_head_publication_produces_zero_writer_calls",
        ],
        "full_pytest_node_ids": [
            "tests/agent/test_pr4_repair_vertical_postgres.py::test_v2_stream_checkpoint_success_then_accepted_failure_has_no_public_success"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7a_telemetry_foundation.py::test_accepted_head_failure_has_no_completed_outcome"
        ],
        "user_visible_assertion": "orphan state emits no public final success",
        "durable_database_assertion": "PostgreSQL vertical keeps orphan noncanonical",
        "accepted_head_assertion": (
            "publication failure is observed and canonical head is unchanged"
        ),
        "checkpoint_assertion": "official internal checkpoint may succeed independently",
        "memory_assertion": "Writer receives zero calls without accepted publication",
        "required_telemetry": "checkpoint success, accepted-head failure, and orphan signal",
    },
    "C7": {
        "injection_point": "subprocess death after saver commit",
        "pytest_node_ids": [
            "tests/agent/test_pr7b_chaos_postgres.py::test_c7_process_death_after_internal_checkpoint_recovers_exact_accepted_cursor"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7b_chaos_postgres.py::test_c7_process_death_after_internal_checkpoint_recovers_exact_accepted_cursor"
        ],
        "user_visible_assertion": "restart resolves only the exact published cursor",
        "durable_database_assertion": "accepted cursor remains persisted across subprocess death",
        "accepted_head_assertion": "recovery loads the previously published accepted cursor",
        "checkpoint_assertion": "newer orphan checkpoint differs and is not selected",
        "memory_assertion": "not exercised by this crash subcase",
        "required_telemetry": "checkpoint and exact-cursor recovery signal",
    },
    "C8": {
        "injection_point": "business commit followed by delivery/process loss",
        "pytest_node_ids": [
            "tests/agent/test_pr7b_chaos_postgres.py::test_c8_business_commit_then_process_death_retries_same_resource_once",
            "tests/agent/test_pr4_repair_vertical_postgres.py::test_v2_disconnect_after_confirmed_commit_recovers_one_canonical_mutation",
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr4_repair_vertical_postgres.py::test_v2_disconnect_after_confirmed_commit_recovers_one_canonical_mutation"
        ],
        "user_visible_assertion": "retry/status recovers one canonical business result",
        "durable_database_assertion": (
            "lower-layer subprocess and Agent confirmed-write cases each prove one work order"
        ),
        "accepted_head_assertion": (
            "Agent subcase recovers the accepted confirmed result after delivery loss"
        ),
        "checkpoint_assertion": (
            "Agent subcase is canonical; process-death subcase is lower-layer only"
        ),
        "memory_assertion": "no duplicate accepted business episode is inferred",
        "required_telemetry": "idempotent replay and accepted confirmed-write recovery",
    },
    "C9": {
        "injection_point": "approval consume and business transaction failure window",
        "pytest_node_ids": [
            "tests/test_p0_concurrency_atomicity.py::test_approval_consume_atomic_with_business_mutation"
        ],
        "full_pytest_node_ids": [
            "tests/test_p0_postgres_concurrency.py::test_consume_rollback_on_business_failure"
        ],
        "telemetry_node_ids": [
            "tests/test_p0_concurrency_atomicity.py::test_approval_consume_atomic_with_business_mutation"
        ],
        "user_visible_assertion": "failed business mutation cannot consume approval",
        "durable_database_assertion": "approval and mutation roll back in the same UoW",
        "accepted_head_assertion": "no accepted business success follows rollback",
        "checkpoint_assertion": "not the injection boundary",
        "memory_assertion": "no completed business episode follows rollback",
        "required_telemetry": "approval rollback/consume contention outcome",
    },
    "C10": {
        "injection_point": "Memory Writer persistence failure",
        "pytest_node_ids": [
            "tests/agent/test_pr7b_chaos.py::test_c10_memory_writer_persistence_failure_does_not_rollback_accepted_turn"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7b_chaos.py::test_c10_memory_writer_persistence_failure_does_not_rollback_accepted_turn"
        ],
        "user_visible_assertion": "accepted business/turn result remains successful",
        "durable_database_assertion": (
            "accepted result is not rolled back by optional Writer failure"
        ),
        "accepted_head_assertion": "accepted turn remains canonical",
        "checkpoint_assertion": "not the injection boundary",
        "memory_assertion": "Writer failure creates no false Memory success",
        "required_telemetry": "Writer persistence failure and degraded Memory mode",
    },
    "C11": {
        "injection_point": "concurrent PostgreSQL approval consume",
        "pytest_node_ids": [
            "tests/test_p0_postgres_concurrency.py::test_double_consume_produces_one_consumed"
        ],
        "telemetry_node_ids": [
            "tests/test_p0_postgres_concurrency.py::test_double_consume_produces_one_consumed"
        ],
        "user_visible_assertion": (
            "one contender consumes and the other observes the canonical outcome"
        ),
        "durable_database_assertion": "one approval consume and one mutation maximum",
        "accepted_head_assertion": "not directly exercised by the approval-store race",
        "checkpoint_assertion": "not applicable",
        "memory_assertion": "not applicable",
        "required_telemetry": "approval consume contention",
    },
    "C12": {
        "injection_point": "lease replacement and stale worker fence",
        "pytest_node_ids": [
            "tests/test_p0_concurrency_atomicity.py::test_assert_run_fence_rejects_stale_fence",
            "tests/agent/test_pr7b_chaos.py::test_c12_runner_rejects_stale_candidate_before_canonical_publication",
        ],
        "full_pytest_node_ids": [
            "tests/test_p0_postgres_concurrency.py::test_stale_fence_writer_rejected"
        ],
        "telemetry_node_ids": [
            "tests/agent/test_pr7b_chaos.py::test_c12_runner_rejects_stale_candidate_before_canonical_publication"
        ],
        "user_visible_assertion": "stale worker fails closed",
        "durable_database_assertion": "PostgreSQL writer rejects stale fence mutation",
        "accepted_head_assertion": "stale worker cannot publish accepted state",
        "checkpoint_assertion": "stale worker cannot make its state canonical",
        "memory_assertion": "stale outcome cannot become accepted Writer evidence",
        "required_telemetry": "lease loss and fence rejection",
    },
}

_ASSERTION_KEYS = (
    "user_visible_assertion",
    "durable_database_assertion",
    "accepted_head_assertion",
    "checkpoint_assertion",
    "memory_assertion",
)
DRILL_ASSERTION_NODES: dict[str, dict[str, tuple[str, ...]]] = {
    "C1": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C1"]["pytest_node_ids"]),
        "durable_database_assertion": (),
        "accepted_head_assertion": (),
        "checkpoint_assertion": (),
        "memory_assertion": (),
    },
    "C2": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C2"]["pytest_node_ids"]),
        "durable_database_assertion": (),
        "accepted_head_assertion": (),
        "checkpoint_assertion": (),
        "memory_assertion": (),
    },
    "C3": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C3"]["pytest_node_ids"]),
        "durable_database_assertion": (),
        "accepted_head_assertion": (),
        "checkpoint_assertion": (),
        "memory_assertion": tuple(DRILL_MANIFEST["C3"]["pytest_node_ids"]),
    },
    "C4": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C4"]["pytest_node_ids"]),
        "durable_database_assertion": tuple(DRILL_MANIFEST["C4"]["pytest_node_ids"]),
        "accepted_head_assertion": (),
        "checkpoint_assertion": tuple(DRILL_MANIFEST["C4"]["pytest_node_ids"]),
        "memory_assertion": (),
    },
    "C5": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C5"]["pytest_node_ids"]),
        "durable_database_assertion": (),
        "accepted_head_assertion": tuple(DRILL_MANIFEST["C5"]["pytest_node_ids"]),
        "checkpoint_assertion": tuple(DRILL_MANIFEST["C5"]["pytest_node_ids"]),
        "memory_assertion": (),
    },
    "C6": {
        "user_visible_assertion": (DRILL_MANIFEST["C6"]["pytest_node_ids"][0],),
        "durable_database_assertion": tuple(DRILL_MANIFEST["C6"]["full_pytest_node_ids"]),
        "accepted_head_assertion": (DRILL_MANIFEST["C6"]["pytest_node_ids"][0],),
        "checkpoint_assertion": tuple(DRILL_MANIFEST["C6"]["full_pytest_node_ids"]),
        "memory_assertion": (DRILL_MANIFEST["C6"]["pytest_node_ids"][1],),
    },
    "C7": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C7"]["pytest_node_ids"]),
        "durable_database_assertion": tuple(DRILL_MANIFEST["C7"]["pytest_node_ids"]),
        "accepted_head_assertion": tuple(DRILL_MANIFEST["C7"]["pytest_node_ids"]),
        "checkpoint_assertion": tuple(DRILL_MANIFEST["C7"]["pytest_node_ids"]),
        "memory_assertion": (),
    },
    "C8": {
        "user_visible_assertion": (DRILL_MANIFEST["C8"]["pytest_node_ids"][1],),
        "durable_database_assertion": tuple(DRILL_MANIFEST["C8"]["pytest_node_ids"]),
        "accepted_head_assertion": (DRILL_MANIFEST["C8"]["pytest_node_ids"][1],),
        "checkpoint_assertion": (),
        "memory_assertion": (),
    },
    "C9": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C9"]["pytest_node_ids"]),
        "durable_database_assertion": tuple(DRILL_MANIFEST["C9"]["full_pytest_node_ids"]),
        "accepted_head_assertion": (),
        "checkpoint_assertion": (),
        "memory_assertion": (),
    },
    "C10": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C10"]["pytest_node_ids"]),
        "durable_database_assertion": (),
        "accepted_head_assertion": tuple(DRILL_MANIFEST["C10"]["pytest_node_ids"]),
        "checkpoint_assertion": (),
        "memory_assertion": tuple(DRILL_MANIFEST["C10"]["pytest_node_ids"]),
    },
    "C11": {
        "user_visible_assertion": tuple(DRILL_MANIFEST["C11"]["pytest_node_ids"]),
        "durable_database_assertion": tuple(DRILL_MANIFEST["C11"]["pytest_node_ids"]),
        "accepted_head_assertion": (),
        "checkpoint_assertion": (),
        "memory_assertion": (),
    },
    "C12": {
        "user_visible_assertion": (DRILL_MANIFEST["C12"]["pytest_node_ids"][1],),
        "durable_database_assertion": tuple(DRILL_MANIFEST["C12"]["full_pytest_node_ids"]),
        "accepted_head_assertion": (DRILL_MANIFEST["C12"]["pytest_node_ids"][1],),
        "checkpoint_assertion": (DRILL_MANIFEST["C12"]["pytest_node_ids"][1],),
        "memory_assertion": (DRILL_MANIFEST["C12"]["pytest_node_ids"][1],),
    },
}


def evaluate(
    campaign: str,
    *,
    requested_sha: str | None = None,
    server_observability_url: str | None = None,
    campaign_id: str | None = None,
) -> GateEvidence:
    started = utc_now()
    release_sha, dirty = repository_state(ROOT, requested_sha)
    full = campaign == "full"
    campaign_id = campaign_id or os.getenv("PR7B_CHAOS_CAMPAIGN_ID") or uuid4().hex
    if not _CAMPAIGN_ID.fullmatch(campaign_id):
        raise ValueError("chaos campaign ID must be 32 lowercase hexadecimal characters")
    selected = frozenset(DRILL_MANIFEST) if full else SAFE_DRILLS
    target_results, counts, limitations, receipt_cases, receipts = _run_fault_campaign(
        selected, full=full, campaign_id=campaign_id
    )
    remote_cases = (
        _chaos_observability_cases(
            server_observability_url,
            release_sha=release_sha,
            started_at=started,
            campaign_id=campaign_id,
        )
        if full
        else selected
    )
    telemetry_cases = receipt_cases.intersection(remote_cases)
    drills = derive_drill_evidence(
        target_results,
        selected=selected,
        telemetry_cases=telemetry_cases,
        full=full,
        campaign_receipts=receipts,
    )
    status = derive_gate_status(drills, selected=selected, full=full, dirty=dirty)
    if dirty:
        limitations = (*limitations, "tracked checkout is dirty")
    elif full and status is GateStatus.NOT_RUN:
        limitations = (*limitations, "one or more drills lack exact test or telemetry evidence")
    elif not full:
        limitations = (*limitations, "C4, C7, C8, and C11 require the full PostgreSQL campaign")
    return GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate="CHAOS_GATE" if full else "CHAOS_HARNESS_SMOKE",
        status=status,
        release_sha=release_sha,
        git_dirty=dirty,
        environment=os.getenv("PR7B_ENVIRONMENT", "ci"),
        started_at=started,
        ended_at=utc_now(),
        dataset_version="pr7b-chaos-c1-c12-v2",
        configuration={
            "campaign": campaign,
            "chaos_campaign_id": campaign_id,
            "campaign_receipts": receipts,
            "fault_profile_version": "pr7b-chaos-v2",
            "drills": drills,
        },
        sample_counts={
            **counts,
            "drill_passes": sum(item["status"] == "PASS" for item in drills.values()),
            "drill_failures": sum(item["status"] == "FAIL" for item in drills.values()),
            "drill_not_run": sum(item["status"] == "NOT_RUN" for item in drills.values()),
        },
        metrics={
            "process_death_cases_executed": int(full and drills["C7"]["execution_status"] == "PASS")
            + int(full and drills["C8"]["execution_status"] == "PASS"),
        },
        hard_gates={case: item["status"] == "PASS" for case, item in drills.items()},
        limitations=limitations,
    )


def derive_drill_evidence(
    target_results: dict[str, str],
    *,
    selected: frozenset[str],
    telemetry_cases: frozenset[str],
    full: bool,
    campaign_receipts: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    campaign_receipts = campaign_receipts or {}
    evidence: dict[str, dict[str, Any]] = {}
    for case, definition in DRILL_MANIFEST.items():
        nodes = list(definition["pytest_node_ids"])
        if full:
            nodes.extend(definition.get("full_pytest_node_ids", []))
        node_statuses = [target_results.get(node, "NOT_RUN") for node in nodes]
        assertion_evidence = {
            key: _assertion_evidence(
                definition[key],
                DRILL_ASSERTION_NODES[case][key],
                effective_nodes=frozenset(nodes),
                target_results=target_results,
                selected=case in selected,
            )
            for key in _ASSERTION_KEYS
        }
        assertion_statuses = [item["status"] for item in assertion_evidence.values()]
        if case not in selected:
            status = "NOT_RUN"
        elif "FAIL" in node_statuses or "FAIL" in assertion_statuses:
            status = "FAIL"
        elif (
            "NOT_RUN" in node_statuses
            or "NOT_RUN" in assertion_statuses
            or case not in telemetry_cases
        ):
            status = "NOT_RUN"
        else:
            status = "PASS"
        execution_status = _node_execution_status(node_statuses, case in selected)
        evidence[case] = {
            "injection_point": definition["injection_point"],
            "pytest_node_ids": nodes,
            "execution_status": execution_status,
            **assertion_evidence,
            "telemetry_evidence": {
                "required_signal": definition["required_telemetry"],
                "campaign_bound_pytest_nodes": campaign_receipts.get(case, []),
                "status": "PASS" if case in telemetry_cases and case in selected else "NOT_RUN",
            },
            "status": status,
        }
    return evidence


def derive_gate_status(
    drills: dict[str, dict[str, Any]],
    *,
    selected: frozenset[str],
    full: bool,
    dirty: bool,
) -> GateStatus:
    """Fail closed from independently derived drill evidence."""
    statuses = [str(drills[case].get("status", "NOT_RUN")) for case in selected]
    if dirty:
        return GateStatus.NOT_RUN
    if "FAIL" in statuses:
        return GateStatus.FAIL
    if not statuses or any(status != "PASS" for status in statuses):
        return GateStatus.NOT_RUN if full else GateStatus.FAIL
    return GateStatus.PASS


def _selected_targets(selected: frozenset[str], *, full: bool) -> list[str]:
    targets: list[str] = []
    for case in selected:
        definition = DRILL_MANIFEST[case]
        targets.extend(definition["pytest_node_ids"])
        if full:
            targets.extend(definition.get("full_pytest_node_ids", []))
    return targets


def _run_fault_campaign(
    selected: frozenset[str], *, full: bool, campaign_id: str
) -> tuple[dict[str, str], dict[str, int], tuple[str, ...], frozenset[str], dict[str, list[str]]]:
    results: dict[str, str] = {}
    totals: Counter[str] = Counter()
    limitations: list[str] = []
    receipts: dict[str, list[str]] = {}
    previous = {name: os.environ.get(name) for name in _CAMPAIGN_ENV_NAMES}
    os.environ["PR7B_CHAOS_CAMPAIGN_ID"] = campaign_id
    try:
        with tempfile.TemporaryDirectory(prefix="pr7b-chaos-receipts-") as temp:
            for case in sorted(selected):
                nodes = list(DRILL_MANIFEST[case]["pytest_node_ids"])
                if full:
                    nodes.extend(DRILL_MANIFEST[case].get("full_pytest_node_ids", []))
                receipts[case] = []
                for index, node in enumerate(nodes):
                    path = Path(temp) / f"{case}-{index}.json"
                    os.environ["PR7B_CHAOS_CASE_ID"] = case
                    os.environ["PR7B_CHAOS_RECEIPT_PATH"] = str(path)
                    os.environ["PR7B_CHAOS_TARGET_NODE_ID"] = node
                    node_results, counts, notes = run_pytest_targets(
                        ROOT,
                        [node],
                        extra_args=("-p", "testing.pr7b.chaos_pytest_plugin"),
                    )
                    results.update(node_results)
                    totals.update(counts)
                    limitations.extend(notes)
                    if _valid_campaign_receipt(path, campaign_id, case, node):
                        receipts[case].append(node)
                    elif (
                        node_results.get(node) == "PASS"
                        and node in DRILL_MANIFEST[case]["telemetry_node_ids"]
                    ):
                        limitations.append(
                            f"{node}: causal actual-component telemetry receipt unavailable"
                        )
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    receipt_cases = frozenset(
        case
        for case in selected
        if set(DRILL_MANIFEST[case]["telemetry_node_ids"]).issubset(receipts.get(case, ()))
    )
    return results, dict(totals), tuple(limitations), receipt_cases, receipts


_CAMPAIGN_ENV_NAMES = (
    "PR7B_CHAOS_CAMPAIGN_ID",
    "PR7B_CHAOS_CASE_ID",
    "PR7B_CHAOS_RECEIPT_PATH",
    "PR7B_CHAOS_TARGET_NODE_ID",
)


def _case_targets(case: str, *, full: bool) -> list[str]:
    nodes = list(DRILL_MANIFEST[case]["pytest_node_ids"])
    if full:
        nodes.extend(DRILL_MANIFEST[case].get("full_pytest_node_ids", []))
    return nodes


def _valid_campaign_receipt(path: Path, campaign_id: str, case: str, node: str) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(value, dict) or any(
        value.get(key) != expected
        for key, expected in {
            "campaign_id": campaign_id,
            "case_id": case,
            "pytest_node_id": node,
        }.items()
    ):
        return False
    actual = value.get("actual_component_signals")
    if not isinstance(actual, list):
        return False
    bounded = [
        {
            "name": signal.get("name"),
            "attributes": signal.get("attributes", {}),
            "production_origin": str(signal.get("source_module", "")).startswith("property_agent."),
        }
        for signal in actual
        if isinstance(signal, dict)
    ]
    matches = matching_signals(case, bounded)
    return len(matches) == len(actual) and bool(matches)


def _node_execution_status(statuses: list[str], selected: bool) -> str:
    if not selected or "NOT_RUN" in statuses:
        return "NOT_RUN"
    return "FAIL" if "FAIL" in statuses else "PASS"


def _assertion_evidence(
    scope: str,
    configured_nodes: tuple[str, ...],
    *,
    effective_nodes: frozenset[str],
    target_results: dict[str, str],
    selected: bool,
) -> dict[str, Any]:
    nodes = [node for node in configured_nodes if node in effective_nodes]
    if not configured_nodes or (selected and not nodes):
        status = "NOT_APPLICABLE"
    else:
        status = _node_execution_status(
            [target_results.get(node, "NOT_RUN") for node in nodes], selected
        )
    return {"scope": scope, "pytest_node_ids": nodes, "status": status}


def _chaos_observability_cases(
    url: str | None, *, release_sha: str, started_at: str, campaign_id: str
) -> frozenset[str]:
    if not url:
        return frozenset()
    headers = {"Authorization": f"Bearer {os.getenv('PR7B_OTEL_SUMMARY_TOKEN', '')}"}
    try:
        response = httpx.get(
            url,
            headers=headers,
            params={
                "release_sha": release_sha,
                "started_at": started_at,
                "gate": "chaos",
                "chaos_campaign_id": campaign_id,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError):
        return frozenset()
    if (
        not isinstance(document, dict)
        or document.get("release_sha") != release_sha
        or document.get("chaos_campaign_id") != campaign_id
    ):
        return frozenset()
    signals = document.get("case_signals", {})
    return frozenset(case for case in DRILL_MANIFEST if int(signals.get(case, 0)) > 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", choices=("safe", "full"), default="safe")
    parser.add_argument("--sha")
    parser.add_argument("--server-observability-url")
    parser.add_argument("--campaign-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = evaluate(
        args.campaign,
        requested_sha=args.sha,
        server_observability_url=args.server_observability_url,
        campaign_id=args.campaign_id,
    )
    write_evidence(args.output, evidence)
    print(f"{evidence.gate}={evidence.status.value}")
    return 0 if evidence.status is GateStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
