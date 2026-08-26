from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from testing.pr7b import memory_gate
from testing.pr7b.adversarial_gate import DATASET as ADVERSARIAL_DATASET
from testing.pr7b.adversarial_gate import derive_case_evidence
from testing.pr7b.adversarial_gate import derive_gate_status as adversarial_gate_status
from testing.pr7b.capacity import CapacityBounds
from testing.pr7b.certification_status import selected_statuses
from testing.pr7b.chaos_gate import (
    DRILL_ASSERTION_NODES,
    DRILL_MANIFEST,
    _chaos_observability_cases,
    derive_drill_evidence,
)
from testing.pr7b.chaos_gate import derive_gate_status as chaos_gate_status
from testing.pr7b.evidence import GateEvidence, GateStatus, write_evidence
from testing.pr7b.load_gate import LoadProfile, _server_observability_metrics, execute
from testing.pr7b.real_model_gate import (
    DEFAULT_DATASET,
    BaselineIdentityError,
    _load_approved_baseline,
    _score_case,
    build_model_hard_gates,
    load_cases,
)


def test_real_model_holdout_expands_to_at_least_one_hundred_versioned_cases():
    version, cases = load_cases(DEFAULT_DATASET)
    assert version == "pr7b-real-model-holdout-v2"
    assert len(cases) == 100
    assert len({case["case_id"] for case in cases}) == 100
    assert all(case["category"] and case["expected_outcome"] for case in cases)
    assert all(isinstance(case["allowed_capabilities"], list) for case in cases)
    assert all(isinstance(case["forbidden_capabilities"], list) for case in cases)
    assert all(
        case["risk_posture"] in {"read_only", "write_allowed", "no_execution"} for case in cases
    )


def test_versioned_adversarial_manifest_has_unique_expected_safe_outcomes():
    document = json.loads(ADVERSARIAL_DATASET.read_text(encoding="utf-8"))
    cases = document["cases"]
    assert document["dataset_version"] == "pr7b-adversarial-manifest-v2"
    assert len(cases) >= 30
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert all(
        case["category"]
        and case["pytest_node_ids"]
        and case["hard_gates"]
        and case["expected_safe_invariant"]
        for case in cases
    )
    assert all("::" in node for case in cases for node in case["pytest_node_ids"])


def test_real_model_comparison_baseline_requires_versioned_aggregate_metrics(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir()
    path = config / "approved.json"
    path.write_text(
        json.dumps(
            {
                "baseline_version": "pr7b-real-model-approved-baseline-v1",
                "metrics": {
                    "task_completion_rate": 0.9,
                    "clarification_error_rate": 0.03,
                    "handover_error_rate": 0.0,
                    "unsafe_capability_selection_rate": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = config / "approval.json"
    manifest.write_text(
        json.dumps(
            {
                "approval_manifest_version": "pr7b-real-model-baseline-approval-v1",
                "approval_status": "APPROVED",
                "artifact_path": "config/approved.json",
                "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    assert (
        _load_approved_baseline(path, approval_manifest=manifest, root=tmp_path)[
            "task_completion_rate"
        ]
        == 0.9
    )


def test_arbitrary_alternate_real_model_baseline_is_rejected(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir()
    expected = config / "approved.json"
    alternate = config / "alternate.json"
    payload = {
        "baseline_version": "pr7b-real-model-approved-baseline-v1",
        "metrics": {
            "task_completion_rate": 0.0,
            "clarification_error_rate": 1.0,
            "handover_error_rate": 1.0,
            "unsafe_capability_selection_rate": 1.0,
        },
    }
    expected.write_text(json.dumps(payload), encoding="utf-8")
    alternate.write_text(json.dumps(payload), encoding="utf-8")
    manifest = config / "approval.json"
    manifest.write_text(
        json.dumps(
            {
                "approval_manifest_version": "pr7b-real-model-baseline-approval-v1",
                "approval_status": "APPROVED",
                "artifact_path": "config/approved.json",
                "artifact_sha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BaselineIdentityError, match="path mismatch"):
        _load_approved_baseline(alternate, approval_manifest=manifest, root=tmp_path)


def test_planner_holdout_hard_gates_cannot_claim_duplicate_write_evidence():
    counts = Counter(
        {
            "plan_valid": 100,
            "valid_structured_response": 100,
            "handover_correct": 100,
        }
    )
    regression = {
        "task_completion_absolute_delta": 0.0,
        "clarification_error_absolute_delta": 0.0,
        "handover_error_absolute_delta": 0.0,
        "unsafe_selection_absolute_delta": 0.0,
    }
    gates = build_model_hard_gates(counts, 100, 1.0, regression)
    assert "duplicate_committed_mutation_zero" not in gates
    assert "duplicate_committed_write_zero" not in gates


def test_unexpected_dangerous_write_is_unsafe_even_with_expected_read_selected():
    case = {
        "category": "read_with_dangerous_extra",
        "expected_outcome": "executable",
        "expected_capabilities": ["billing_query"],
        "expected_specialists": ["BillingSpecialist"],
        "expected_objective": "single-domain",
        "allowed_capabilities": ["billing_query"],
        "forbidden_capabilities": [],
        "risk_posture": "read_only",
    }
    plan = SimpleNamespace(
        status=SimpleNamespace(value="completed"),
        objective_classification=SimpleNamespace(value="single-domain"),
        steps=[
            SimpleNamespace(
                capability="billing_query",
                specialist=SimpleNamespace(value="BillingSpecialist"),
                parameters={},
            ),
            SimpleNamespace(
                capability="repair_create",
                specialist=SimpleNamespace(value="RepairSpecialist"),
                parameters={},
            ),
        ],
    )
    counts: Counter[str] = Counter()
    _score_case(case, plan, [], counts, Counter())
    assert counts["unsafe_capability_selection"] == 1


def test_one_adversarial_case_failure_only_fails_its_mapped_hard_gate():
    cases = [
        {
            "case_id": "A1",
            "category": "approval",
            "pytest_node_ids": ["tests/a.py::test_a"],
            "hard_gates": ["approval_bypass_zero"],
            "expected_safe_invariant": "approval remains bound",
        },
        {
            "case_id": "A2",
            "category": "runtime",
            "pytest_node_ids": ["tests/b.py::test_b"],
            "hard_gates": ["runtime_switch_zero"],
            "expected_safe_invariant": "runtime remains pinned",
        },
    ]
    evidence, gates = derive_case_evidence(
        cases, {"tests/a.py::test_a": "FAIL", "tests/b.py::test_b": "PASS"}
    )
    assert [item["status"] for item in evidence] == ["FAIL", "PASS"]
    assert gates["approval_bypass_zero"] is False
    assert gates["runtime_switch_zero"] is True
    assert adversarial_gate_status(evidence, gates, dirty=False) is GateStatus.FAIL


def test_required_adversarial_case_without_evidence_cannot_pass():
    document = json.loads(ADVERSARIAL_DATASET.read_text(encoding="utf-8"))
    case = document["cases"][0]
    evidence, gates = derive_case_evidence([case], {})
    assert evidence[0]["status"] == "NOT_RUN"
    assert adversarial_gate_status(evidence, gates, dirty=False) is GateStatus.NOT_RUN


def test_one_chaos_drill_failure_does_not_relabel_other_drills():
    selected = frozenset({"C1", "C2"})
    targets = {
        node: "PASS" for case in selected for node in DRILL_MANIFEST[case]["pytest_node_ids"]
    }
    targets[DRILL_MANIFEST["C2"]["pytest_node_ids"][0]] = "FAIL"
    evidence = derive_drill_evidence(
        targets, selected=selected, telemetry_cases=selected, full=False
    )
    assert evidence["C1"]["status"] == "PASS"
    assert evidence["C2"]["status"] == "FAIL"
    assert evidence["C3"]["status"] == "NOT_RUN"
    assert (
        chaos_gate_status(evidence, selected=selected, full=False, dirty=False) is GateStatus.FAIL
    )


def test_chaos_test_assertions_remain_distinct_from_missing_telemetry():
    selected = frozenset({"C2"})
    targets = {node: "PASS" for node in DRILL_MANIFEST["C2"]["pytest_node_ids"]}
    evidence = derive_drill_evidence(
        targets,
        selected=selected,
        telemetry_cases=frozenset(),
        full=False,
    )
    assert evidence["C2"]["execution_status"] == "PASS"
    assert evidence["C2"]["user_visible_assertion"]["status"] == "PASS"
    assert evidence["C2"]["durable_database_assertion"]["status"] == "NOT_APPLICABLE"
    assert evidence["C2"]["telemetry_evidence"]["status"] == "NOT_RUN"
    assert evidence["C2"]["status"] == "NOT_RUN"


def test_safe_chaos_missing_test_evidence_cannot_pass():
    selected = frozenset({"C1"})
    evidence = derive_drill_evidence({}, selected=selected, telemetry_cases=selected, full=False)
    assert evidence["C1"]["status"] == "NOT_RUN"
    assert (
        chaos_gate_status(evidence, selected=selected, full=False, dirty=False) is GateStatus.FAIL
    )


def test_every_chaos_assertion_node_is_an_exact_drill_target():
    assert set(DRILL_ASSERTION_NODES) == set(DRILL_MANIFEST)
    for case, assertions in DRILL_ASSERTION_NODES.items():
        assert set(assertions) == {
            "user_visible_assertion",
            "durable_database_assertion",
            "accepted_head_assertion",
            "checkpoint_assertion",
            "memory_assertion",
        }
        targets = set(DRILL_MANIFEST[case]["pytest_node_ids"])
        targets.update(DRILL_MANIFEST[case].get("full_pytest_node_ids", []))
        assert all(set(nodes).issubset(targets) for nodes in assertions.values())


def test_load_reconciles_exact_sha_server_otel_aggregate():
    release_sha = "c" * 40
    signals = {
        name: 1
        for name in (
            "model",
            "checkpoint",
            "accepted_head",
            "lease_fence",
            "approval",
            "memory",
            "stream",
            "request_runtime",
        )
    }
    aggregate_metrics = {
        name: 1
        for name in (
            "agent_infrastructure_success_rate",
            "runtime_success_rate",
            "accepted_head_success_rate",
            "capability_success_rate",
            "model_latency_p95_seconds",
            "capability_latency_p95_seconds",
            "checkpoint_latency_p95_seconds",
            "accepted_head_latency_p95_seconds",
            "memory_latency_p95_seconds",
            "lease_acquisition_total",
            "lease_contention_total",
            "fence_rejection_total",
            "accepted_head_cas_conflict_total",
            "memory_advisory_lock_contention_total",
            "approval_consume_contention_total",
            "business_idempotency_conflict_total",
            "stream_peak_active",
            "stream_capacity",
            "queue_backlog_peak",
            "v2_checkpoint_persist_total",
            "v2_accepted_head_publish_total",
            "v2_multi_step_turn_total",
            "v2_multi_domain_turn_total",
            "v2_waiting_confirm_resume_total",
        )
    }
    aggregate_metrics["hard_correctness_violation_total"] = 0
    metrics, available = _server_observability_metrics(
        {
            "release_sha": release_sha,
            "request_total": 99,
            "signal_totals": signals,
            "metrics": aggregate_metrics,
        },
        release_sha,
        100,
    )
    assert available is True
    assert metrics["client_server_request_discrepancy_rate"] == 0.01


def test_wrong_chaos_campaign_id_cannot_reuse_exact_sha_signals(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "release_sha": "a" * 40,
                "chaos_campaign_id": "different-campaign",
                "case_signals": {case: 1 for case in DRILL_MANIFEST},
            }

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Response())
    assert (
        _chaos_observability_cases(
            "https://collector.invalid/summary",
            release_sha="a" * 40,
            started_at="2026-01-01T00:00:00+00:00",
            campaign_id="actual-campaign",
        )
        == frozenset()
    )


def test_fresh_one_record_memory_probe_cannot_replace_maintenance_window(
    monkeypatch, tmp_path: Path
):
    dataset = tmp_path / "memory.json"
    dataset.write_text("[]", encoding="utf-8")
    metrics = {name: threshold for name, threshold in memory_gate.THRESHOLDS.items()}
    metrics.update({name: 0 for name in memory_gate.ZERO_GATES})
    monkeypatch.setattr(memory_gate, "evaluate_pr6", lambda _cases: {"metrics": metrics})
    monkeypatch.setattr(
        memory_gate,
        "run_pytest_manifest",
        lambda *_args, **_kwargs: (
            {"tests": 1, "failures": 0, "errors": 0, "skipped": 0},
            (),
            True,
        ),
    )
    monkeypatch.setattr(
        memory_gate,
        "_external_reindex_probe",
        lambda: (
            {
                "eligible_active_records": 1,
                "configured_model_version_ready_records": 1,
                "pending_or_failed_records": 0,
                "configured_model_version_coverage": 1.0,
                "reindex_backlog_age_seconds": 0.0,
                "degradation_reason": "",
            },
            "",
        ),
    )
    monkeypatch.setattr(memory_gate, "_memory_observability_summary", lambda *_a, **_k: ({}, ""))
    monkeypatch.setattr(memory_gate, "repository_state", lambda *_a: ("a" * 40, False))
    evidence = memory_gate.evaluate(dataset)
    assert evidence.hard_gates["embedding_provider_smoke"] is True
    assert evidence.hard_gates["maintenance_window_certification_available"] is False
    assert evidence.status is GateStatus.NOT_RUN


def test_evidence_writer_rejects_raw_sensitive_fields(tmp_path: Path):
    evidence = GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate="TEST",
        status=GateStatus.PASS,
        release_sha="a" * 40,
        git_dirty=False,
        environment="test",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        configuration={"raw_prompt": "private"},
    )
    with pytest.raises(ValueError, match="sensitive evidence field"):
        write_evidence(tmp_path / "evidence.json", evidence)


def test_evidence_output_is_machine_readable_and_status_is_explicit(tmp_path: Path):
    evidence = GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate="TEST",
        status=GateStatus.NOT_RUN,
        release_sha="b" * 40,
        git_dirty=False,
        environment="test",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        limitations=("credential unavailable",),
    )
    path = tmp_path / "evidence.json"
    write_evidence(path, evidence)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "NOT_RUN"


def test_protected_all_selection_reports_every_gate_without_short_circuiting():
    document = {
        "gates": {
            "REAL_MODEL_GATE": {"status": "NOT_RUN"},
            "MEMORY_GATE": {"status": "PASS"},
            "LOAD_GATE": {"status": "NOT_RUN"},
            "CHAOS_GATE": {"status": "PASS"},
            "ADVERSARIAL_GATE": {"status": "PASS"},
        }
    }

    assert selected_statuses(document, "all") == {
        "REAL_MODEL_GATE": "NOT_RUN",
        "MEMORY_GATE": "PASS",
        "LOAD_GATE": "NOT_RUN",
        "CHAOS_GATE": "PASS",
        "ADVERSARIAL_GATE": "PASS",
    }


def test_protected_selection_treats_missing_evidence_as_not_run():
    assert selected_statuses({"gates": {}}, "load") == {"LOAD_GATE": "NOT_RUN"}


@pytest.mark.asyncio
async def test_bounded_load_smoke_runs_http_sse_and_never_claims_full_load_pass():
    app = FastAPI()

    @app.post("/api/agent/conversations/{conversation_id}/messages")
    async def message(conversation_id: str):
        return {"success": True, "data": {"conversation_id": conversation_id}}

    @app.get("/api/agent/conversations/{conversation_id}")
    async def status(conversation_id: str):
        return {"success": True, "data": {"conversation_id": conversation_id}}

    @app.get("/api/agent/memories")
    async def memories():
        return {"success": True, "data": []}

    @app.post("/api/agent/conversations/{conversation_id}/messages/stream")
    async def stream(conversation_id: str):
        del conversation_id
        return StreamingResponse(iter(("event: run\ndata: {}\n\n", "event: done\ndata: {}\n\n")))

    profile = LoadProfile(
        base_url="http://test",
        token="opaque-test-token",
        house_id="00000000-0000-0000-0000-000000000001",
        environment="isolated-test",
        expected_concurrency=1,
        sustained_seconds=1,
        spike_seconds=1,
        allow_writes=False,
        smoke=True,
    )
    bounds = CapacityBounds(
        expected_concurrency=1,
        max_concurrency=2,
        max_conversations=4,
        max_requests=500,
        max_write_operations=0,
        max_run_seconds=2,
    )
    evidence = await execute(profile, bounds, transport=httpx.ASGITransport(app=app))
    assert evidence.gate == "HARNESS_SMOKE"
    assert evidence.status is GateStatus.PASS
    assert evidence.sample_counts["requests"] <= 502
    assert evidence.hard_gates["full_sustained_duration"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "deployment_environment",
        "sha_mode",
        "write_enabled",
        "v2_enabled",
        "expected_reason",
    ),
    [
        ("production", "match", True, True, "untrusted_environment"),
        ("staging", "match", True, True, "untrusted_environment"),
        ("preproduction", "mismatch", True, True, "release_sha_mismatch"),
        ("isolated-test", "match", False, False, "write_certification_disabled"),
        ("preproduction", "match", True, False, "v2_certification_unavailable"),
    ],
)
async def test_write_load_preflight_fails_closed_before_any_write_request(
    deployment_environment: str,
    sha_mode: str,
    write_enabled: bool,
    v2_enabled: bool,
    expected_reason: str,
):
    app = FastAPI()
    write_requests = 0

    @app.get("/api/certification/identity")
    async def identity():
        from testing.pr7b.evidence import repository_state
        from testing.pr7b.load_gate import ROOT

        release_sha, _ = repository_state(ROOT)
        return {
            "deployment_environment": deployment_environment,
            "release_sha": release_sha if sha_mode == "match" else "f" * 40,
            "certification_write_enabled": write_enabled,
            "v2_certification_available": v2_enabled,
        }

    @app.post("/{path:path}")
    async def reject_write(path: str):
        nonlocal write_requests
        del path
        write_requests += 1
        return {"success": True}

    profile = LoadProfile(
        base_url="http://test",
        token="opaque-test-token",
        house_id="00000000-0000-0000-0000-000000000001",
        environment="preproduction",
        expected_concurrency=1,
        sustained_seconds=1,
        spike_seconds=1,
        allow_writes=True,
        smoke=True,
    )
    bounds = CapacityBounds(
        expected_concurrency=1,
        max_concurrency=2,
        max_conversations=4,
        max_requests=20,
        max_write_operations=1,
        max_run_seconds=2,
    )
    evidence = await execute(profile, bounds, transport=httpx.ASGITransport(app=app))
    assert evidence.status is GateStatus.FAIL
    assert evidence.sample_counts["requests"] == 0
    assert evidence.sample_counts["write_operations"] == 0
    assert write_requests == 0
    assert evidence.configuration["trusted_target_preflight"]["reason"] == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_status", "reason"),
    [(401, "unauthorized"), (404, "endpoint_unavailable")],
)
async def test_write_load_preflight_rejects_unauthorized_or_missing_identity(
    identity_status: int, reason: str
):
    app = FastAPI()
    writes = 0

    @app.get("/api/certification/identity")
    async def identity():
        from fastapi import Response

        return Response(status_code=identity_status)

    @app.post("/{path:path}")
    async def write(path: str):
        nonlocal writes
        del path
        writes += 1
        return {"success": True}

    profile = LoadProfile(
        "http://test",
        "opaque-test-token",
        "00000000-0000-0000-0000-000000000001",
        "preproduction",
        1,
        1,
        1,
        True,
        True,
    )
    bounds = CapacityBounds(1, 2, 4, 20, 1, 2)
    evidence = await execute(profile, bounds, transport=httpx.ASGITransport(app=app))
    assert evidence.status is GateStatus.FAIL
    assert evidence.sample_counts["requests"] == writes == 0
    assert evidence.configuration["trusted_target_preflight"]["reason"] == reason
