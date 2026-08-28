from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from property_agent.agent.approval_authority import (
    APPROVAL_SIGNATURE_VERSION,
    TrustedApprovalAuthority,
)
from property_agent.agent.infrastructure.models import (
    AgentActionApprovalModel,
    AgentCheckpointModel,
    ConversationModel,
)
from property_agent.agent.v1_drain import (
    DrainInvariantError,
    DrainPolicy,
    V1DrainClassification,
    V1DrainFacts,
    build_v1_drain_inventory,
    classify_v1_conversation,
    drain_policy_signature_payload,
    expire_abandoned_v1,
)

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"e" * 32)
PUBLIC_KEY = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
AUTHORITY = TrustedApprovalAuthority(
    authority_id="release-board:test",
    public_key_base64=base64.b64encode(PUBLIC_KEY).decode("ascii"),
)


def _facts(**changes) -> V1DrainFacts:
    values = {
        "conversation_id": "conversation-1",
        "community_id": "00000000-0000-0000-0000-000000000010",
        "runtime_version": "v1",
        "status": "ACTIVE",
        "created_at": NOW - timedelta(days=40),
        "last_activity_at": NOW - timedelta(days=1),
        "checkpoint_pending_confirm": False,
        "checkpoint_state": None,
        "open_approval_count": 0,
        "persisted_drain_state": None,
    }
    values.update(changes)
    return V1DrainFacts(**values)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, V1DrainClassification.LIVE_ACTIVE),
        ({"status": "WAITING_CONFIRM"}, V1DrainClassification.LIVE_WAITING_CONFIRM),
        ({"status": "HANDOVER"}, V1DrainClassification.LIVE_HANDOVER),
        (
            {"status": "CLOSED", "checkpoint_state": {"plan": {"status": "COMPLETED"}}},
            V1DrainClassification.TERMINAL_COMPLETED,
        ),
        (
            {"status": "CLOSED", "checkpoint_state": {"plan": {"status": "FAILED"}}},
            V1DrainClassification.TERMINAL_FAILED,
        ),
        (
            {"status": "CLOSED", "persisted_drain_state": "EXPIRED"},
            V1DrainClassification.EXPIRED,
        ),
        (
            {"last_activity_at": NOW - timedelta(days=31)},
            V1DrainClassification.ABANDONED_CANDIDATE,
        ),
        (
            {"status": "ACTIVE", "open_approval_count": 1},
            V1DrainClassification.UNKNOWN,
        ),
        ({"status": "CLOSED"}, V1DrainClassification.UNKNOWN),
    ],
)
def test_canonical_classifier_is_conservative(changes, expected) -> None:
    result = classify_v1_conversation(
        _facts(**changes), now=NOW, abandoned_after=timedelta(days=30)
    )
    assert result.classification is expected
    if expected in {V1DrainClassification.UNKNOWN, V1DrainClassification.ABANDONED_CANDIDATE}:
        assert result.resumable is True


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        ConversationModel.__table__,
        AgentCheckpointModel.__table__,
        AgentActionApprovalModel.__table__,
    ):
        table.create(engine)
    return Session(engine, expire_on_commit=False)


def _conversation(conversation_id: str, *, status: str = "ACTIVE") -> ConversationModel:
    return ConversationModel(
        conversation_id=conversation_id,
        actor_id=UUID("00000000-0000-0000-0000-000000000001"),
        community_id=UUID("00000000-0000-0000-0000-000000000010"),
        status=status,
        runtime_version="v1",
        created_at=NOW - timedelta(days=60),
        updated_at=NOW - timedelta(days=40),
        last_message_at=NOW - timedelta(days=40),
    )


def _signed_policy() -> DrainPolicy:
    policy = DrainPolicy(
        policy_version="drain-policy-v1",
        max_inactive_seconds=30 * 24 * 3600,
        max_actions=10,
        approved_at=NOW.isoformat(),
        approval_authority_id=AUTHORITY.authority_id,
        approval_signature_version=APPROVAL_SIGNATURE_VERSION,
    )
    signature = PRIVATE_KEY.sign(drain_policy_signature_payload(policy))
    return replace(policy, approval_signature=base64.b64encode(signature).decode("ascii"))


def test_inventory_is_grouped_and_marks_abandoned_as_resumable() -> None:
    session = _session()
    session.add_all([_conversation("old"), _conversation("waiting", status="WAITING_CONFIRM")])
    session.commit()
    report = build_v1_drain_inventory(
        session,
        release_sha="a" * 40,
        now=NOW,
        abandoned_after=timedelta(days=30),
    )
    assert report.total_v1 == 2
    assert report.counts[V1DrainClassification.ABANDONED_CANDIDATE.value] == 1
    assert report.counts[V1DrainClassification.LIVE_WAITING_CONFIRM.value] == 1
    assert list(report.community_counts.values()) == [2]
    assert "actor" not in repr(report)


def test_expiry_defaults_to_dry_run_and_is_idempotent_when_executed() -> None:
    session = _session()
    conversation = _conversation("old")
    session.add(conversation)
    session.commit()
    dry_run = expire_abandoned_v1(
        session,
        policy=_signed_policy(),
        approval_authority=AUTHORITY,
        now=NOW,
    )
    assert dry_run.dry_run is True
    assert dry_run.expired == 1
    assert conversation.status == "ACTIVE"
    executed = expire_abandoned_v1(
        session,
        policy=_signed_policy(),
        approval_authority=AUTHORITY,
        now=NOW,
        dry_run=False,
    )
    session.commit()
    assert executed.expired == 1
    assert conversation.status == "CLOSED"
    assert conversation.v1_drain_state == "EXPIRED"
    repeated = expire_abandoned_v1(
        session,
        policy=_signed_policy(),
        approval_authority=AUTHORITY,
        now=NOW,
        dry_run=False,
    )
    assert repeated.expired == 0


def test_unsigned_policy_and_pending_state_fail_closed() -> None:
    session = _session()
    conversation = _conversation("pending")
    session.add(conversation)
    session.add(
        AgentCheckpointModel(
            thread_id="pending",
            state={},
            pending_confirm=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.commit()
    with pytest.raises(DrainInvariantError, match="not approved"):
        expire_abandoned_v1(
            session,
            policy=replace(_signed_policy(), approval_signature=""),
            approval_authority=AUTHORITY,
            now=NOW,
        )
    with pytest.raises(DrainInvariantError, match="inconsistent"):
        expire_abandoned_v1(
            session,
            policy=_signed_policy(),
            approval_authority=AUTHORITY,
            now=NOW,
        )
