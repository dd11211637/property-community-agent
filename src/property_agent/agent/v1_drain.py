"""Canonical PR7-E v1 inventory, classification, and bounded expiry tooling."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from property_agent.agent.approval_authority import (
    TrustedApprovalAuthority,
    verify_approval_signature,
)
from property_agent.agent.infrastructure.models import (
    AgentActionApprovalModel,
    AgentCheckpointModel,
    ConversationModel,
)

V1_DRAIN_CLASSIFIER_VERSION = "pr7e-v1-drain-classifier-v1"
V1_DRAIN_POLICY_VERSION = "pr7e-v1-drain-policy-v1"
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LIVE = frozenset(
    {
        "LIVE_ACTIVE",
        "LIVE_WAITING_CONFIRM",
        "LIVE_HANDOVER",
        "ABANDONED_CANDIDATE",
        "UNKNOWN",
    }
)


class V1DrainClassification(StrEnum):
    LIVE_ACTIVE = "LIVE_ACTIVE"
    LIVE_WAITING_CONFIRM = "LIVE_WAITING_CONFIRM"
    LIVE_HANDOVER = "LIVE_HANDOVER"
    TERMINAL_COMPLETED = "TERMINAL_COMPLETED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    EXPIRED = "EXPIRED"
    ABANDONED_CANDIDATE = "ABANDONED_CANDIDATE"
    UNKNOWN = "UNKNOWN"


class DrainInvariantError(RuntimeError):
    """Raised when a bounded write encounters inconsistent authoritative state."""


@dataclass(frozen=True, slots=True)
class V1DrainFacts:
    conversation_id: str
    community_id: str
    runtime_version: str
    status: str
    created_at: datetime
    last_activity_at: datetime
    checkpoint_pending_confirm: bool
    checkpoint_state: dict[str, Any] | None
    open_approval_count: int
    persisted_drain_state: str | None = None


@dataclass(frozen=True, slots=True)
class ClassifiedV1Conversation:
    classification: V1DrainClassification
    resumable: bool
    reason: str


@dataclass(frozen=True, slots=True)
class V1DrainInventory:
    release_sha: str
    database_snapshot: str
    generated_at: str
    classifier_version: str
    total_v1: int
    counts: dict[str, int]
    community_counts: dict[str, int]
    oldest_live_created_at: str | None
    oldest_live_activity_at: str | None
    complete: bool


@dataclass(frozen=True, slots=True)
class DrainPolicy:
    policy_version: str
    max_inactive_seconds: int
    max_actions: int
    approved_at: str
    approval_authority_id: str
    approval_signature_version: str
    approval_signature: str = ""
    schema_version: str = V1_DRAIN_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class DrainActionReceipt:
    policy_version: str
    dry_run: bool
    examined: int
    expired: int
    record_references: tuple[str, ...]
    generated_at: str


def classify_v1_conversation(
    facts: V1DrainFacts,
    *,
    now: datetime,
    abandoned_after: timedelta,
) -> ClassifiedV1Conversation:
    """Classify from authoritative lifecycle/checkpoint/approval facts."""
    if facts.runtime_version != "v1":
        return _classified(V1DrainClassification.UNKNOWN, "runtime is not v1")
    if facts.persisted_drain_state == "EXPIRED":
        if (
            facts.status != "CLOSED"
            or facts.open_approval_count
            or facts.checkpoint_pending_confirm
        ):
            return _classified(V1DrainClassification.UNKNOWN, "expired record is still pending")
        return _classified(V1DrainClassification.EXPIRED, "approved expiry is persisted")
    if facts.status == "WAITING_CONFIRM":
        return _classified(V1DrainClassification.LIVE_WAITING_CONFIRM, "waiting confirmation")
    if facts.status == "HANDOVER":
        return _classified(V1DrainClassification.LIVE_HANDOVER, "human handover remains live")
    if facts.open_approval_count or facts.checkpoint_pending_confirm:
        return _classified(V1DrainClassification.UNKNOWN, "pending state conflicts with lifecycle")
    if facts.status == "CLOSED":
        outcome = _checkpoint_outcome(facts.checkpoint_state)
        if outcome == "FAILED":
            return _classified(V1DrainClassification.TERMINAL_FAILED, "accepted outcome failed")
        if outcome in {"COMPLETED", "CANCELLED"}:
            return _classified(
                V1DrainClassification.TERMINAL_COMPLETED, "accepted outcome terminal"
            )
        return _classified(V1DrainClassification.UNKNOWN, "closed outcome is not explicit")
    if facts.status != "ACTIVE":
        return _classified(V1DrainClassification.UNKNOWN, "unknown lifecycle status")
    if _as_utc(now) - _as_utc(facts.last_activity_at) >= abandoned_after:
        return _classified(
            V1DrainClassification.ABANDONED_CANDIDATE,
            "inactive with no pending confirmation or approval",
        )
    return _classified(V1DrainClassification.LIVE_ACTIVE, "active within inactivity policy")


def build_v1_drain_inventory(
    session: Session,
    *,
    release_sha: str,
    now: datetime,
    abandoned_after: timedelta,
    maximum_records: int = 100_000,
) -> V1DrainInventory:
    """Read trusted database state and return a bounded, privacy-minimized report."""
    rows = session.execute(_inventory_query(maximum_records + 1)).all()
    complete = len(rows) <= maximum_records
    rows = rows[:maximum_records]
    counts: Counter[str] = Counter()
    community_counts: defaultdict[str, int] = defaultdict(int)
    live_created: list[datetime] = []
    live_activity: list[datetime] = []
    for row in rows:
        facts = _facts_from_row(row)
        result = classify_v1_conversation(facts, now=now, abandoned_after=abandoned_after)
        counts[result.classification.value] += 1
        community_counts[facts.community_id] += 1
        if result.resumable:
            live_created.append(facts.created_at)
            live_activity.append(facts.last_activity_at)
    return V1DrainInventory(
        release_sha=release_sha,
        database_snapshot=_database_snapshot(session),
        generated_at=now.astimezone(timezone.utc).isoformat(),
        classifier_version=V1_DRAIN_CLASSIFIER_VERSION,
        total_v1=len(rows),
        counts=dict(sorted(counts.items())),
        community_counts=dict(sorted(community_counts.items())),
        oldest_live_created_at=_oldest(live_created),
        oldest_live_activity_at=_oldest(live_activity),
        complete=complete,
    )


def drain_policy_signature_payload(policy: DrainPolicy) -> bytes:
    payload = {
        "schema_version": policy.schema_version,
        "policy_version": policy.policy_version,
        "max_inactive_seconds": policy.max_inactive_seconds,
        "max_actions": policy.max_actions,
        "approved_at": policy.approved_at,
        "approval_authority_id": policy.approval_authority_id,
        "approval_signature_version": policy.approval_signature_version,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_drain_policy(data: dict[str, Any]) -> DrainPolicy:
    return DrainPolicy(
        schema_version=str(data.get("schema_version", "")),
        policy_version=str(data.get("policy_version", "")),
        max_inactive_seconds=int(data.get("max_inactive_seconds", 0)),
        max_actions=int(data.get("max_actions", 0)),
        approved_at=str(data.get("approved_at", "")),
        approval_authority_id=str(data.get("approval_authority_id", "")),
        approval_signature_version=str(data.get("approval_signature_version", "")),
        approval_signature=str(data.get("approval_signature", "")),
    )


def verify_drain_policy(
    policy: DrainPolicy, *, approval_authority: TrustedApprovalAuthority
) -> bool:
    if policy.schema_version != V1_DRAIN_POLICY_VERSION:
        return False
    if not _VERSION.fullmatch(policy.policy_version):
        return False
    if policy.max_inactive_seconds <= 0 or not 1 <= policy.max_actions <= 100:
        return False
    try:
        approved_at = datetime.fromisoformat(policy.approved_at)
        if approved_at.utcoffset() is None:
            return False
    except (TypeError, ValueError):
        return False
    return verify_approval_signature(
        drain_policy_signature_payload(policy),
        authority_id=policy.approval_authority_id,
        signature_version=policy.approval_signature_version,
        signature_base64=policy.approval_signature,
        authority=approval_authority,
    )


def expire_abandoned_v1(
    session: Session,
    *,
    policy: DrainPolicy,
    approval_authority: TrustedApprovalAuthority,
    now: datetime,
    dry_run: bool = True,
) -> DrainActionReceipt:
    """Expire only verified candidates; caller owns commit/rollback."""
    if not verify_drain_policy(policy, approval_authority=approval_authority):
        raise DrainInvariantError("drain policy is not approved by the trusted authority")
    cutoff = now - timedelta(seconds=policy.max_inactive_seconds)
    rows = session.execute(_expiry_query(cutoff, policy.max_actions)).all()
    references: list[str] = []
    expired = 0
    for row in rows:
        facts = _facts_from_row(row)
        result = classify_v1_conversation(
            facts,
            now=now,
            abandoned_after=timedelta(seconds=policy.max_inactive_seconds),
        )
        if result.classification is V1DrainClassification.UNKNOWN:
            raise DrainInvariantError("inconsistent v1 state encountered; drain stopped")
        if result.classification is not V1DrainClassification.ABANDONED_CANDIDATE:
            continue
        references.append(_record_reference(facts.conversation_id))
        expired += 1
        if not dry_run:
            conversation = row[0]
            conversation.status = "CLOSED"
            conversation.closed_at = now
            conversation.v1_drain_state = "EXPIRED"
            conversation.v1_drain_policy_version = policy.policy_version
            conversation.v1_drain_idempotency_key = _idempotency_key(
                policy.policy_version, facts.conversation_id
            )
            conversation.v1_drained_at = now
    return DrainActionReceipt(
        policy_version=policy.policy_version,
        dry_run=dry_run,
        examined=len(rows),
        expired=expired,
        record_references=tuple(references),
        generated_at=now.astimezone(timezone.utc).isoformat(),
    )


def _inventory_query(limit: int):
    open_approvals = _open_approval_counts()
    return (
        select(
            ConversationModel,
            AgentCheckpointModel,
            func.coalesce(open_approvals.c.open_count, 0).label("open_count"),
        )
        .outerjoin(
            AgentCheckpointModel,
            AgentCheckpointModel.thread_id == ConversationModel.conversation_id,
        )
        .outerjoin(
            open_approvals, open_approvals.c.conversation_id == ConversationModel.conversation_id
        )
        .where(ConversationModel.runtime_version == "v1")
        .order_by(ConversationModel.conversation_id)
        .limit(limit)
    )


def _expiry_query(cutoff: datetime, limit: int):
    return (
        _inventory_query(limit)
        .where(ConversationModel.status == "ACTIVE")
        .where(ConversationModel.v1_drain_state.is_(None))
        .where(
            func.coalesce(ConversationModel.last_message_at, ConversationModel.updated_at) <= cutoff
        )
        .with_for_update(skip_locked=True, of=ConversationModel)
    )


def _open_approval_counts():
    return (
        select(
            AgentActionApprovalModel.conversation_id,
            func.count(AgentActionApprovalModel.id).label("open_count"),
        )
        .where(AgentActionApprovalModel.status.in_(("PENDING", "APPROVED")))
        .group_by(AgentActionApprovalModel.conversation_id)
        .subquery()
    )


def _facts_from_row(row: Any) -> V1DrainFacts:
    conversation = row[0]
    checkpoint = row[1]
    return V1DrainFacts(
        conversation_id=conversation.conversation_id,
        community_id=str(conversation.community_id),
        runtime_version=conversation.runtime_version,
        status=conversation.status,
        created_at=conversation.created_at,
        last_activity_at=conversation.last_message_at or conversation.updated_at,
        checkpoint_pending_confirm=bool(checkpoint and checkpoint.pending_confirm),
        checkpoint_state=checkpoint.state if checkpoint else None,
        open_approval_count=int(row.open_count),
        persisted_drain_state=conversation.v1_drain_state,
    )


def _checkpoint_outcome(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    plan = state.get("plan")
    if isinstance(plan, dict) and isinstance(plan.get("status"), str):
        return plan["status"].upper()
    value = state.get("accepted_outcome")
    return value.upper() if isinstance(value, str) else ""


def _classified(classification: V1DrainClassification, reason: str):
    return ClassifiedV1Conversation(
        classification=classification,
        resumable=classification.value in _LIVE,
        reason=reason,
    )


def _database_snapshot(session: Session) -> str:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return "non-postgresql-test-snapshot"
    return str(session.execute(text("SELECT txid_current_snapshot()")).scalar_one())


def _record_reference(conversation_id: str) -> str:
    return hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:20]


def _idempotency_key(policy_version: str, conversation_id: str) -> str:
    payload = f"{policy_version}:{conversation_id}".encode()
    return "v1-drain:" + hashlib.sha256(payload).hexdigest()


def _oldest(values: list[datetime]) -> str | None:
    return min(_as_utc(value) for value in values).isoformat() if values else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ClassifiedV1Conversation",
    "DrainActionReceipt",
    "DrainInvariantError",
    "DrainPolicy",
    "V1DrainClassification",
    "V1DrainFacts",
    "V1DrainInventory",
    "build_v1_drain_inventory",
    "classify_v1_conversation",
    "drain_policy_signature_payload",
    "expire_abandoned_v1",
    "parse_drain_policy",
    "verify_drain_policy",
]
