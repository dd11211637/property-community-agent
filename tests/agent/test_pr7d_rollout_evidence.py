from __future__ import annotations

import base64
from dataclasses import replace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from property_agent.agent.approval_authority import (
    APPROVAL_SIGNATURE_VERSION,
    TrustedApprovalAuthority,
)
from property_agent.agent.model_release import ModelReleaseIdentity
from property_agent.agent.rollout_evidence import (
    EvidenceStatus,
    RollbackReceipt,
    RolloutEvidence,
    RolloutStage,
    evaluate_promotion_gate,
    rollback_receipt_signature_payload,
    rollout_evidence_signature_payload,
    verify_rollback_receipt,
)

PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"d" * 32)
PUBLIC_KEY = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
AUTHORITY = TrustedApprovalAuthority(
    authority_id="release-board:test",
    public_key_base64=base64.b64encode(PUBLIC_KEY).decode("ascii"),
)
GATES = {
    name: "PASS"
    for name in (
        "quality",
        "postgres",
        "browser",
        "real_model",
        "memory",
        "load",
        "chaos",
        "adversarial",
    )
}


def _actual() -> ModelReleaseIdentity:
    return ModelReleaseIdentity(
        primary_provider="deepseek",
        model="deepseek-v4-flash",
        provider_config_version="deepseek-bounded-retry-v1",
        provider_config_fingerprint="a1" * 32,
        prompt_contract_version="semantic-planner-pr5-v1",
        model_release_evidence_reference="pr7b-real-model:" + "b2" * 32,
        primary_provider_ready=True,
        fallback_policy_version="deepseek-to-deterministic-v1",
    )


def _evidence(stage: RolloutStage = RolloutStage.R0) -> RolloutEvidence:
    actual = _actual()
    basis_points = {
        RolloutStage.R0: 0,
        RolloutStage.R1: 500,
        RolloutStage.R2: 2500,
        RolloutStage.R3: 5000,
        RolloutStage.R4: 10_000,
        RolloutStage.R5: 10_000,
    }[stage]
    return RolloutEvidence(
        stage=stage,
        release_sha="a" * 40,
        deployment_environment="preproduction",
        rollout_basis_points=basis_points,
        rollout_config_version="rollout-v1",
        provider=actual.primary_provider,
        model=actual.model,
        provider_config_fingerprint=actual.provider_config_fingerprint,
        prompt_contract_version=actual.prompt_contract_version,
        model_release_evidence_reference=actual.model_release_evidence_reference,
        observation_started_at="2026-08-01T00:00:00+00:00",
        observation_ended_at="2026-08-20T00:00:00+00:00",
        cumulative_v2_turns=10_000,
        incident_count=0,
        rollback_exercised=True,
        hard_gates=GATES,
        evidence_references=("ci:123", "dashboard:window-1"),
        approval_status="APPROVED",
        approval_authority_id=AUTHORITY.authority_id,
        approval_signature_version=APPROVAL_SIGNATURE_VERSION,
    )


def _signed(evidence: RolloutEvidence) -> RolloutEvidence:
    signature = PRIVATE_KEY.sign(rollout_evidence_signature_payload(evidence))
    return replace(evidence, approval_signature=base64.b64encode(signature).decode("ascii"))


def _decision(evidence: RolloutEvidence):
    return evaluate_promotion_gate(
        evidence,
        actual_model_release=_actual(),
        approval_authority=AUTHORITY,
    )


def test_complete_signed_r0_evidence_passes_without_enabling_rollout() -> None:
    evidence = _signed(_evidence())
    assert _decision(evidence).status is EvidenceStatus.PASS
    assert evidence.rollout_basis_points == 0


def test_operator_integrity_without_authority_signature_is_pending() -> None:
    decision = _decision(_evidence())
    assert decision.status is EvidenceStatus.PENDING
    assert any("signature" in reason for reason in decision.reasons)


def test_not_run_gate_cannot_be_reported_as_pass() -> None:
    evidence = replace(_evidence(), hard_gates={**GATES, "real_model": "NOT_RUN"})
    decision = _decision(_signed(evidence))
    assert decision.status is EvidenceStatus.PENDING
    assert any("real_model" in reason for reason in decision.reasons)


def test_r1_real_time_and_turn_requirements_are_not_synthesized() -> None:
    evidence = replace(
        _evidence(RolloutStage.R1),
        observation_ended_at="2026-08-01T01:00:00+00:00",
        cumulative_v2_turns=199,
    )
    decision = _decision(_signed(evidence))
    assert decision.status is EvidenceStatus.PENDING
    assert any("duration" in reason for reason in decision.reasons)
    assert any("turn count" in reason for reason in decision.reasons)


def test_certified_identity_drift_fails_closed() -> None:
    evidence = replace(_evidence(), provider_config_fingerprint="c3" * 32)
    decision = _decision(_signed(evidence))
    assert decision.status is EvidenceStatus.PENDING
    assert any("fingerprint" in reason for reason in decision.reasons)


def test_signature_does_not_survive_evidence_tampering() -> None:
    evidence = _signed(_evidence())
    tampered = replace(evidence, incident_count=1)
    decision = _decision(tampered)
    assert decision.status is EvidenceStatus.PENDING
    assert any("signature" in reason for reason in decision.reasons)
    assert any("incident" in reason for reason in decision.reasons)


def test_signed_rollback_receipt_proves_decrease_to_zero() -> None:
    receipt = RollbackReceipt(
        release_sha="a" * 40,
        old_basis_points=500,
        new_basis_points=0,
        old_config_version="r1-v1",
        new_config_version="rollback-v1",
        reason="incident_rollback",
        change_reference="incident:123",
        observed_at="2026-08-20T00:00:00+00:00",
        approval_authority_id=AUTHORITY.authority_id,
        approval_signature_version=APPROVAL_SIGNATURE_VERSION,
    )
    signature = PRIVATE_KEY.sign(rollback_receipt_signature_payload(receipt))
    signed = replace(receipt, approval_signature=base64.b64encode(signature).decode("ascii"))
    assert verify_rollback_receipt(signed, approval_authority=AUTHORITY) is True
    assert (
        verify_rollback_receipt(
            replace(signed, new_basis_points=500),
            approval_authority=AUTHORITY,
        )
        is False
    )
