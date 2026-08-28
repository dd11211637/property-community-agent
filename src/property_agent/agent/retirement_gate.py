"""PR7-F static, dynamic, database, and approval retirement interlocks."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from property_agent.agent.approval_authority import (
    TrustedApprovalAuthority,
    verify_approval_signature,
)
from property_agent.agent.rollout_evidence import (
    EvidenceStatus,
    PromotionGateDecision,
    RolloutStage,
)
from property_agent.agent.v1_drain import V1DrainClassification, V1DrainInventory

RETIREMENT_EVIDENCE_VERSION = "pr7f-retirement-evidence-v1"
DYNAMIC_ZERO_VERSION = "pr7f-dynamic-zero-v1"
RETIREMENT_APPROVAL_VERSION = "pr7f-retirement-approval-v1"
_SHA = re.compile(r"^[a-f0-9]{40}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SELECTOR_FILES = (
    "agent/application/composition.py",
    "agent/application/facade.py",
    "agent/application/graph_engine.py",
    "agent/application/runner.py",
    "agent/runtime_rollout.py",
    "agent/runtime_version.py",
    "config.py",
)


class RetirementGateStatus(StrEnum):
    PASS = "PASS"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class StaticV1Dependency:
    path: str
    line: int
    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class StaticInterlockReport:
    scanner_version: str
    dependencies: tuple[StaticV1Dependency, ...]

    @property
    def passed(self) -> bool:
        return not self.dependencies


@dataclass(frozen=True, slots=True)
class DynamicZeroEvidence:
    release_sha: str
    rollout_config_version: str
    observation_started_at: str
    observation_ended_at: str
    representative_new_conversation_count: int
    new_v1_assignment_count: int
    approval_authority_id: str
    approval_signature_version: str
    approval_signature: str = ""
    schema_version: str = DYNAMIC_ZERO_VERSION


@dataclass(frozen=True, slots=True)
class RetirementApproval:
    release_sha: str
    rollback_strategy_version: str
    retention_approval_reference: str
    approved_at: str
    approval_authority_id: str
    approval_signature_version: str
    approval_signature: str = ""
    schema_version: str = RETIREMENT_APPROVAL_VERSION


@dataclass(frozen=True, slots=True)
class RetirementEvidence:
    release_sha: str
    r5_decision: PromotionGateDecision
    static_interlock: StaticInterlockReport
    dynamic_zero: DynamicZeroEvidence
    drain_inventory: V1DrainInventory
    retirement_approval: RetirementApproval
    runtime_switch_violation_count: int
    unresolved_blocker_count: int
    rollback_exercised: bool
    schema_version: str = RETIREMENT_EVIDENCE_VERSION


@dataclass(frozen=True, slots=True)
class RetirementGateDecision:
    status: RetirementGateStatus
    reasons: tuple[str, ...]


def scan_static_v1_dependencies(src_root: Path) -> StaticInterlockReport:
    """AST-scan production selector/dispatch files; data history is intentionally excluded."""
    dependencies: list[StaticV1Dependency] = []
    for relative in _SELECTOR_FILES:
        path = src_root / "property_agent" / relative
        if not path.is_file():
            dependencies.append(StaticV1Dependency(relative, 0, "missing", "file"))
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            dependencies.append(StaticV1Dependency(relative, 0, "unreadable", "file"))
            continue
        dependencies.extend(_scan_tree(relative, tree))
    return StaticInterlockReport("pr7f-static-no-v1-v1", tuple(dependencies))


def dynamic_zero_signature_payload(evidence: DynamicZeroEvidence) -> bytes:
    return _canonical_bytes(evidence, exclude="approval_signature")


def retirement_approval_signature_payload(approval: RetirementApproval) -> bytes:
    return _canonical_bytes(approval, exclude="approval_signature")


def evaluate_retirement_gate(
    evidence: RetirementEvidence,
    *,
    approval_authority: TrustedApprovalAuthority,
) -> RetirementGateDecision:
    """Return PASS only when every real-world retirement interlock is proven."""
    reasons: list[str] = []
    if evidence.schema_version != RETIREMENT_EVIDENCE_VERSION:
        reasons.append("unsupported retirement evidence schema")
    if not _SHA.fullmatch(evidence.release_sha):
        reasons.append("retirement release_sha is invalid")
    if evidence.r5_decision.stage is not RolloutStage.R5:
        reasons.append("R5 evidence is not the final observation stage")
    if evidence.r5_decision.status is not EvidenceStatus.PASS:
        reasons.append("R5 production observation is incomplete")
    if not evidence.static_interlock.passed:
        reasons.append("static production paths can still select or dispatch v1")
    reasons.extend(
        _dynamic_zero_reasons(evidence.dynamic_zero, evidence.release_sha, approval_authority)
    )
    reasons.extend(_database_reasons(evidence.drain_inventory))
    if not _verify_retirement_approval(
        evidence.retirement_approval, evidence.release_sha, approval_authority
    ):
        reasons.append("trusted retirement and rollback-target approval is missing")
    if evidence.runtime_switch_violation_count != 0:
        reasons.append("runtime-switch hard gate is non-zero")
    if evidence.unresolved_blocker_count != 0:
        reasons.append("unresolved severity blocker remains")
    if not evidence.rollback_exercised:
        reasons.append("rollback exercise evidence is missing")
    status = RetirementGateStatus.PASS if not reasons else RetirementGateStatus.PENDING
    return RetirementGateDecision(status=status, reasons=tuple(reasons))


def _scan_tree(relative: str, tree: ast.AST) -> list[StaticV1Dependency]:
    found: list[StaticV1Dependency] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if any("LegacyGraphEngine" in name for name in names):
                found.append(
                    StaticV1Dependency(relative, node.lineno, "legacy_import", "LegacyGraphEngine")
                )
        elif isinstance(node, ast.Name) and node.id == "LegacyGraphEngine":
            found.append(StaticV1Dependency(relative, node.lineno, "legacy_dispatch", node.id))
        elif isinstance(node, ast.Constant) and node.value == "v1":
            found.append(StaticV1Dependency(relative, node.lineno, "v1_selector", "v1"))
    unique = {(item.path, item.line, item.kind, item.value): item for item in found}
    return list(unique.values())


def _dynamic_zero_reasons(
    evidence: DynamicZeroEvidence,
    release_sha: str,
    authority: TrustedApprovalAuthority,
) -> list[str]:
    reasons: list[str] = []
    if evidence.schema_version != DYNAMIC_ZERO_VERSION:
        reasons.append("unsupported dynamic-zero evidence schema")
    if evidence.release_sha != release_sha:
        reasons.append("dynamic-zero evidence release mismatch")
    if not _VERSION.fullmatch(evidence.rollout_config_version):
        reasons.append("dynamic-zero rollout config version is invalid")
    if evidence.new_v1_assignment_count != 0:
        reasons.append("new_v1_assignment_count is non-zero")
    if evidence.representative_new_conversation_count <= 0:
        reasons.append("representative new traffic evidence is missing")
    if not _valid_window(evidence.observation_started_at, evidence.observation_ended_at):
        reasons.append("dynamic-zero observation window is invalid")
    if not verify_approval_signature(
        dynamic_zero_signature_payload(evidence),
        authority_id=evidence.approval_authority_id,
        signature_version=evidence.approval_signature_version,
        signature_base64=evidence.approval_signature,
        authority=authority,
    ):
        reasons.append("dynamic-zero evidence signature is invalid")
    return reasons


def _database_reasons(inventory: V1DrainInventory) -> list[str]:
    if not inventory.complete:
        return ["v1 database inventory is incomplete"]
    blockers = {
        V1DrainClassification.LIVE_ACTIVE.value,
        V1DrainClassification.LIVE_WAITING_CONFIRM.value,
        V1DrainClassification.LIVE_HANDOVER.value,
        V1DrainClassification.ABANDONED_CANDIDATE.value,
        V1DrainClassification.UNKNOWN.value,
    }
    count = sum(inventory.counts.get(name, 0) for name in blockers)
    return [] if count == 0 else [f"database retains {count} resumable v1 conversations"]


def _verify_retirement_approval(
    approval: RetirementApproval,
    release_sha: str,
    authority: TrustedApprovalAuthority,
) -> bool:
    if approval.schema_version != RETIREMENT_APPROVAL_VERSION:
        return False
    if approval.release_sha != release_sha:
        return False
    if not _VERSION.fullmatch(approval.rollback_strategy_version):
        return False
    if not approval.retention_approval_reference:
        return False
    try:
        approved_at = datetime.fromisoformat(approval.approved_at)
        if approved_at.utcoffset() is None:
            return False
    except (TypeError, ValueError):
        return False
    return verify_approval_signature(
        retirement_approval_signature_payload(approval),
        authority_id=approval.approval_authority_id,
        signature_version=approval.approval_signature_version,
        signature_base64=approval.approval_signature,
        authority=authority,
    )


def _valid_window(start: str, end: str) -> bool:
    try:
        started = datetime.fromisoformat(start)
        ended = datetime.fromisoformat(end)
    except (TypeError, ValueError):
        return False
    return started.utcoffset() is not None and ended.utcoffset() is not None and ended > started


def _canonical_bytes(value: Any, *, exclude: str) -> bytes:
    payload = {
        item.name: _json_value(getattr(value, item.name))
        for item in fields(value)
        if item.name != exclude
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


__all__ = [
    "DynamicZeroEvidence",
    "RetirementApproval",
    "RetirementEvidence",
    "RetirementGateDecision",
    "RetirementGateStatus",
    "StaticInterlockReport",
    "StaticV1Dependency",
    "dynamic_zero_signature_payload",
    "evaluate_retirement_gate",
    "retirement_approval_signature_payload",
    "scan_static_v1_dependencies",
]
