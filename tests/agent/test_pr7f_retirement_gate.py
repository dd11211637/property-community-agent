from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from property_agent.agent.approval_authority import (
    APPROVAL_SIGNATURE_VERSION,
    TrustedApprovalAuthority,
)
from property_agent.agent.observability import AgentObservability
from property_agent.agent.retirement_gate import (
    DynamicZeroEvidence,
    RetirementApproval,
    RetirementEvidence,
    RetirementGateStatus,
    StaticInterlockReport,
    dynamic_zero_signature_payload,
    evaluate_retirement_gate,
    retirement_approval_signature_payload,
    scan_static_v1_dependencies,
)
from property_agent.agent.rollout_evidence import (
    EvidenceStatus,
    PromotionGateDecision,
    RolloutStage,
)
from property_agent.agent.runtime_rollout import (
    BucketDecisionClass,
    EligibilityReason,
    RuntimeAssignment,
)
from property_agent.agent.v1_drain import V1DrainInventory

RELEASE_SHA = "a" * 40
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"f" * 32)
PUBLIC_KEY = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
AUTHORITY = TrustedApprovalAuthority(
    "release-board:test", base64.b64encode(PUBLIC_KEY).decode("ascii")
)


def _signed_dynamic() -> DynamicZeroEvidence:
    evidence = DynamicZeroEvidence(
        release_sha=RELEASE_SHA,
        rollout_config_version="r5-v1",
        observation_started_at="2026-08-01T00:00:00+00:00",
        observation_ended_at="2026-08-20T00:00:00+00:00",
        representative_new_conversation_count=10_000,
        new_v1_assignment_count=0,
        approval_authority_id=AUTHORITY.authority_id,
        approval_signature_version=APPROVAL_SIGNATURE_VERSION,
    )
    signature = PRIVATE_KEY.sign(dynamic_zero_signature_payload(evidence))
    return replace(evidence, approval_signature=base64.b64encode(signature).decode("ascii"))


def _signed_approval() -> RetirementApproval:
    approval = RetirementApproval(
        release_sha=RELEASE_SHA,
        rollback_strategy_version="v2-admission-stop-v1",
        retention_approval_reference="retention:approval-1",
        approved_at="2026-08-20T00:00:00+00:00",
        approval_authority_id=AUTHORITY.authority_id,
        approval_signature_version=APPROVAL_SIGNATURE_VERSION,
    )
    signature = PRIVATE_KEY.sign(retirement_approval_signature_payload(approval))
    return replace(approval, approval_signature=base64.b64encode(signature).decode("ascii"))


def _inventory(**changes) -> V1DrainInventory:
    values = {
        "release_sha": RELEASE_SHA,
        "database_snapshot": "1:2:",
        "generated_at": "2026-08-20T00:00:00+00:00",
        "classifier_version": "pr7e-v1-drain-classifier-v1",
        "total_v1": 4,
        "counts": {"TERMINAL_COMPLETED": 3, "EXPIRED": 1},
        "community_counts": {"community:opaque": 4},
        "oldest_live_created_at": None,
        "oldest_live_activity_at": None,
        "complete": True,
    }
    values.update(changes)
    return V1DrainInventory(**values)


def _evidence(**changes) -> RetirementEvidence:
    values = {
        "release_sha": RELEASE_SHA,
        "r5_decision": PromotionGateDecision(EvidenceStatus.PASS, RolloutStage.R5, ()),
        "static_interlock": StaticInterlockReport("pr7f-static-no-v1-v1", ()),
        "dynamic_zero": _signed_dynamic(),
        "drain_inventory": _inventory(),
        "retirement_approval": _signed_approval(),
        "runtime_switch_violation_count": 0,
        "unresolved_blocker_count": 0,
        "rollback_exercised": True,
    }
    values.update(changes)
    return RetirementEvidence(**values)


def test_all_independent_retirement_interlocks_can_pass() -> None:
    decision = evaluate_retirement_gate(_evidence(), approval_authority=AUTHORITY)
    assert decision.status is RetirementGateStatus.PASS
    assert decision.reasons == ()


def test_current_production_static_paths_keep_retirement_pending() -> None:
    src = Path(__file__).resolve().parents[2] / "src"
    report = scan_static_v1_dependencies(src)
    assert report.passed is False
    assert any(item.value in {"v1", "LegacyGraphEngine"} for item in report.dependencies)


def test_snapshot_zero_cannot_replace_dynamic_and_static_evidence() -> None:
    dynamic = replace(_signed_dynamic(), representative_new_conversation_count=0)
    evidence = _evidence(
        dynamic_zero=dynamic,
        drain_inventory=_inventory(total_v1=0, counts={}, community_counts={}),
    )
    decision = evaluate_retirement_gate(evidence, approval_authority=AUTHORITY)
    assert decision.status is RetirementGateStatus.PENDING
    assert any("representative" in reason for reason in decision.reasons)
    assert any("signature" in reason for reason in decision.reasons)


def test_resumable_or_unknown_database_pins_block_retirement() -> None:
    inventory = _inventory(counts={"UNKNOWN": 1, "TERMINAL_COMPLETED": 3})
    decision = evaluate_retirement_gate(
        _evidence(drain_inventory=inventory), approval_authority=AUTHORITY
    )
    assert decision.status is RetirementGateStatus.PENDING
    assert any("resumable" in reason for reason in decision.reasons)


def test_unsigned_human_retirement_approval_fails_closed() -> None:
    approval = replace(_signed_approval(), approval_signature="")
    decision = evaluate_retirement_gate(
        _evidence(retirement_approval=approval), approval_authority=AUTHORITY
    )
    assert decision.status is RetirementGateStatus.PENDING
    assert any("approval" in reason for reason in decision.reasons)


def test_new_v1_assignments_emit_dedicated_dynamic_interlock_counter() -> None:
    observation = AgentObservability.in_memory()
    assignment = RuntimeAssignment(
        runtime_version="v1",
        eligible=True,
        eligibility_reason=EligibilityReason.ELIGIBLE,
        decision_class=BucketDecisionClass.BUCKET_V1,
        bucket=9000,
        rollout_basis_points=500,
        config_version="r1-v1",
        salt_version="salt-v1",
        eligibility_policy_version="eligibility-v1",
    )
    observation.observe_runtime_assignment(assignment)
    points = [
        point for point in observation.points if point.name == "agent_new_v1_assignment_total"
    ]
    assert len(points) == 1
    assert "runtime" not in points[0].attributes
