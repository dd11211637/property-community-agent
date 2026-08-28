"""Versioned, signed PR7-D rollout evidence and promotion gate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from property_agent.agent.approval_authority import (
    TrustedApprovalAuthority,
    verify_approval_signature,
)
from property_agent.agent.model_release import ModelReleaseIdentity

ROLLOUT_EVIDENCE_VERSION = "pr7d-rollout-evidence-v1"
ROLLBACK_RECEIPT_VERSION = "pr7d-rollback-receipt-v1"
_SHA = re.compile(r"^[a-f0-9]{40}$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MANDATORY_RELEASE_GATES = frozenset(
    {
        "quality",
        "postgres",
        "browser",
        "real_model",
        "memory",
        "load",
        "chaos",
        "adversarial",
    }
)


class RolloutStage(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"


class EvidenceStatus(StrEnum):
    PASS = "PASS"
    PENDING = "PENDING"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class StageRequirement:
    basis_points: int
    observation_seconds: int
    cumulative_v2_turns: int
    rollback_required: bool = False


STAGE_REQUIREMENTS = {
    RolloutStage.R0: StageRequirement(0, 0, 0, rollback_required=True),
    RolloutStage.R1: StageRequirement(500, 24 * 3600, 200),
    RolloutStage.R2: StageRequirement(2500, 48 * 3600, 1_000),
    RolloutStage.R3: StageRequirement(5000, 72 * 3600, 5_000),
    RolloutStage.R4: StageRequirement(10_000, 0, 5_000),
    RolloutStage.R5: StageRequirement(10_000, 14 * 24 * 3600, 10_000),
}


@dataclass(frozen=True, slots=True)
class RolloutEvidence:
    stage: RolloutStage
    release_sha: str
    deployment_environment: str
    rollout_basis_points: int
    rollout_config_version: str
    provider: str
    model: str
    provider_config_fingerprint: str
    prompt_contract_version: str
    model_release_evidence_reference: str
    observation_started_at: str
    observation_ended_at: str
    cumulative_v2_turns: int
    incident_count: int
    rollback_exercised: bool
    hard_gates: dict[str, str] = field(default_factory=dict)
    evidence_references: tuple[str, ...] = ()
    approval_status: str = "PENDING"
    approval_authority_id: str = ""
    approval_signature_version: str = ""
    approval_signature: str = ""
    schema_version: str = ROLLOUT_EVIDENCE_VERSION


@dataclass(frozen=True, slots=True)
class PromotionGateDecision:
    status: EvidenceStatus
    stage: RolloutStage
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RollbackReceipt:
    release_sha: str
    old_basis_points: int
    new_basis_points: int
    old_config_version: str
    new_config_version: str
    reason: str
    change_reference: str
    observed_at: str
    approval_authority_id: str
    approval_signature_version: str
    approval_signature: str = ""
    schema_version: str = ROLLBACK_RECEIPT_VERSION


def parse_rollout_evidence(data: dict[str, Any]) -> RolloutEvidence:
    """Parse explicit v1 evidence fields; malformed types fail at the boundary."""
    return RolloutEvidence(
        schema_version=str(data.get("schema_version", "")),
        stage=RolloutStage(str(data.get("stage", "R0"))),
        release_sha=str(data.get("release_sha", "")),
        deployment_environment=str(data.get("deployment_environment", "")),
        rollout_basis_points=int(data.get("rollout_basis_points", -1)),
        rollout_config_version=str(data.get("rollout_config_version", "")),
        provider=str(data.get("provider", "")),
        model=str(data.get("model", "")),
        provider_config_fingerprint=str(data.get("provider_config_fingerprint", "")),
        prompt_contract_version=str(data.get("prompt_contract_version", "")),
        model_release_evidence_reference=str(data.get("model_release_evidence_reference", "")),
        observation_started_at=str(data.get("observation_started_at", "")),
        observation_ended_at=str(data.get("observation_ended_at", "")),
        cumulative_v2_turns=int(data.get("cumulative_v2_turns", 0)),
        incident_count=int(data.get("incident_count", 0)),
        rollback_exercised=bool(data.get("rollback_exercised", False)),
        hard_gates=dict(data.get("hard_gates", {})),
        evidence_references=tuple(data.get("evidence_references", ())),
        approval_status=str(data.get("approval_status", "PENDING")),
        approval_authority_id=str(data.get("approval_authority_id", "")),
        approval_signature_version=str(data.get("approval_signature_version", "")),
        approval_signature=str(data.get("approval_signature", "")),
    )


def rollout_evidence_signature_payload(evidence: RolloutEvidence) -> bytes:
    """Canonical evidence bytes; the signature itself is excluded."""
    payload = {
        name: value
        for name, value in _evidence_dict(evidence).items()
        if name != "approval_signature"
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def rollback_receipt_signature_payload(receipt: RollbackReceipt) -> bytes:
    """Canonical signed rollback receipt fields."""
    payload = {
        "schema_version": receipt.schema_version,
        "release_sha": receipt.release_sha,
        "old_basis_points": receipt.old_basis_points,
        "new_basis_points": receipt.new_basis_points,
        "old_config_version": receipt.old_config_version,
        "new_config_version": receipt.new_config_version,
        "reason": receipt.reason,
        "change_reference": receipt.change_reference,
        "observed_at": receipt.observed_at,
        "approval_authority_id": receipt.approval_authority_id,
        "approval_signature_version": receipt.approval_signature_version,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_rollback_receipt(
    receipt: RollbackReceipt,
    *,
    approval_authority: TrustedApprovalAuthority,
) -> bool:
    """Require a signed, exact-release decrease to the zero assignment target."""
    if receipt.schema_version != ROLLBACK_RECEIPT_VERSION:
        return False
    if not _SHA.fullmatch(receipt.release_sha):
        return False
    if receipt.old_basis_points <= 0 or receipt.new_basis_points != 0:
        return False
    if not _VERSION.fullmatch(receipt.old_config_version):
        return False
    if not _VERSION.fullmatch(receipt.new_config_version):
        return False
    try:
        observed = datetime.fromisoformat(receipt.observed_at)
        if observed.utcoffset() is None:
            return False
    except (TypeError, ValueError):
        return False
    return verify_approval_signature(
        rollback_receipt_signature_payload(receipt),
        authority_id=receipt.approval_authority_id,
        signature_version=receipt.approval_signature_version,
        signature_base64=receipt.approval_signature,
        authority=approval_authority,
    )


def evaluate_promotion_gate(
    evidence: RolloutEvidence,
    *,
    actual_model_release: ModelReleaseIdentity,
    approval_authority: TrustedApprovalAuthority,
) -> PromotionGateDecision:
    """Evaluate immutable evidence; never changes rollout or synthesizes observations."""
    reasons = _validate_identity(evidence, actual_model_release)
    reasons.extend(_validate_observation(evidence))
    reasons.extend(_validate_gates(evidence))
    signed = verify_approval_signature(
        rollout_evidence_signature_payload(evidence),
        authority_id=evidence.approval_authority_id,
        signature_version=evidence.approval_signature_version,
        signature_base64=evidence.approval_signature,
        authority=approval_authority,
    )
    if evidence.approval_status != "APPROVED":
        reasons.append("approval_status is not APPROVED")
    if not signed:
        reasons.append("trusted approval signature is invalid or unavailable")
    status = EvidenceStatus.PASS if not reasons else EvidenceStatus.PENDING
    return PromotionGateDecision(status=status, stage=evidence.stage, reasons=tuple(reasons))


def _validate_identity(evidence: RolloutEvidence, actual: ModelReleaseIdentity) -> list[str]:
    reasons: list[str] = []
    expected = {
        "provider": actual.primary_provider,
        "model": actual.model,
        "provider_config_fingerprint": actual.provider_config_fingerprint,
        "prompt_contract_version": actual.prompt_contract_version,
        "model_release_evidence_reference": actual.model_release_evidence_reference,
    }
    if evidence.schema_version != ROLLOUT_EVIDENCE_VERSION:
        reasons.append("unsupported rollout evidence schema")
    if not _SHA.fullmatch(evidence.release_sha):
        reasons.append("release_sha must be exact lowercase 40-hex")
    if not _VERSION.fullmatch(evidence.rollout_config_version):
        reasons.append("rollout_config_version is invalid")
    if not _FINGERPRINT.fullmatch(evidence.provider_config_fingerprint):
        reasons.append("provider_config_fingerprint is invalid")
    for name, value in expected.items():
        if getattr(evidence, name) != value:
            reasons.append(f"{name} does not match certified production identity")
    if not actual.primary_provider_ready:
        reasons.append("certified primary provider is not ready")
    return reasons


def _validate_observation(evidence: RolloutEvidence) -> list[str]:
    requirement = STAGE_REQUIREMENTS[evidence.stage]
    reasons: list[str] = []
    if evidence.rollout_basis_points != requirement.basis_points:
        reasons.append("rollout basis points do not match stage")
    try:
        started = datetime.fromisoformat(evidence.observation_started_at)
        ended = datetime.fromisoformat(evidence.observation_ended_at)
        duration = (ended - started).total_seconds()
        if started.utcoffset() is None or ended.utcoffset() is None or duration < 0:
            raise ValueError
        if duration < requirement.observation_seconds:
            reasons.append("required observation duration is incomplete")
    except (TypeError, ValueError):
        reasons.append("observation timestamps are invalid")
    if evidence.cumulative_v2_turns < requirement.cumulative_v2_turns:
        reasons.append("required cumulative v2 turn count is incomplete")
    if evidence.incident_count != 0:
        reasons.append("incident-free gate is not satisfied")
    if requirement.rollback_required and not evidence.rollback_exercised:
        reasons.append("rollback exercise evidence is missing")
    return reasons


def _validate_gates(evidence: RolloutEvidence) -> list[str]:
    reasons: list[str] = []
    missing = MANDATORY_RELEASE_GATES - evidence.hard_gates.keys()
    if missing:
        reasons.append(f"mandatory release gates missing: {','.join(sorted(missing))}")
    failed = sorted(name for name, value in evidence.hard_gates.items() if value != "PASS")
    if failed:
        reasons.append(f"release gates are not PASS: {','.join(failed)}")
    if not evidence.evidence_references:
        reasons.append("durable evidence references are missing")
    return reasons


def _evidence_dict(evidence: RolloutEvidence) -> dict[str, Any]:
    return {
        "schema_version": evidence.schema_version,
        "stage": evidence.stage.value,
        "release_sha": evidence.release_sha,
        "deployment_environment": evidence.deployment_environment,
        "rollout_basis_points": evidence.rollout_basis_points,
        "rollout_config_version": evidence.rollout_config_version,
        "provider": evidence.provider,
        "model": evidence.model,
        "provider_config_fingerprint": evidence.provider_config_fingerprint,
        "prompt_contract_version": evidence.prompt_contract_version,
        "model_release_evidence_reference": evidence.model_release_evidence_reference,
        "observation_started_at": evidence.observation_started_at,
        "observation_ended_at": evidence.observation_ended_at,
        "cumulative_v2_turns": evidence.cumulative_v2_turns,
        "incident_count": evidence.incident_count,
        "rollback_exercised": evidence.rollback_exercised,
        "hard_gates": dict(sorted(evidence.hard_gates.items())),
        "evidence_references": list(evidence.evidence_references),
        "approval_status": evidence.approval_status,
        "approval_authority_id": evidence.approval_authority_id,
        "approval_signature_version": evidence.approval_signature_version,
        "approval_signature": evidence.approval_signature,
    }


__all__ = [
    "EvidenceStatus",
    "PromotionGateDecision",
    "ROLLBACK_RECEIPT_VERSION",
    "ROLLOUT_EVIDENCE_VERSION",
    "RolloutEvidence",
    "RollbackReceipt",
    "RolloutStage",
    "STAGE_REQUIREMENTS",
    "evaluate_promotion_gate",
    "parse_rollout_evidence",
    "rollout_evidence_signature_payload",
    "rollback_receipt_signature_payload",
    "verify_rollback_receipt",
]
