from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from testing.pr7b.adversarial_gate import DATASET as ADVERSARIAL_DATASET
from testing.pr7b.capacity import CapacityBounds
from testing.pr7b.certification_status import selected_statuses
from testing.pr7b.evidence import GateEvidence, GateStatus, write_evidence
from testing.pr7b.load_gate import LoadProfile, _server_observability_metrics, execute
from testing.pr7b.real_model_gate import (
    DEFAULT_DATASET,
    _load_approved_baseline,
    load_cases,
)


def test_real_model_holdout_expands_to_at_least_one_hundred_versioned_cases():
    version, cases = load_cases(DEFAULT_DATASET)
    assert version == "pr7b-real-model-holdout-v1"
    assert len(cases) == 100
    assert len({case["case_id"] for case in cases}) == 100
    assert all(case["category"] and case["expected_outcome"] for case in cases)


def test_versioned_adversarial_manifest_has_unique_expected_safe_outcomes():
    document = json.loads(ADVERSARIAL_DATASET.read_text(encoding="utf-8"))
    cases = document["cases"]
    assert document["dataset_version"] == "pr7b-adversarial-manifest-v1"
    assert len(cases) >= 30
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert all(case["category"] and case["safe_outcome"] for case in cases)


def test_real_model_comparison_baseline_requires_versioned_aggregate_metrics(tmp_path: Path):
    path = tmp_path / "approved.json"
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
    assert _load_approved_baseline(path)["task_completion_rate"] == 0.9


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
