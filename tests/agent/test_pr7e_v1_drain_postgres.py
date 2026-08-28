from __future__ import annotations

import base64
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from property_agent.agent.approval_authority import (
    APPROVAL_SIGNATURE_VERSION,
    TrustedApprovalAuthority,
)
from property_agent.agent.infrastructure.models import ConversationModel
from property_agent.agent.v1_drain import (
    DrainPolicy,
    build_v1_drain_inventory,
    drain_policy_signature_payload,
    expire_abandoned_v1,
)
from property_agent.platform.infrastructure.orm_models import Base

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="requires TEST_POSTGRES_URL"),
]


def test_postgres_inventory_snapshot_and_bounded_expiry() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    private_key = Ed25519PrivateKey.from_private_bytes(b"p" * 32)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    authority = TrustedApprovalAuthority(
        "release-board:test", base64.b64encode(public_key).decode("ascii")
    )
    policy = DrainPolicy(
        policy_version="postgres-drain-v1",
        max_inactive_seconds=30 * 24 * 3600,
        max_actions=1,
        approved_at=now.isoformat(),
        approval_authority_id=authority.authority_id,
        approval_signature_version=APPROVAL_SIGNATURE_VERSION,
    )
    signature = private_key.sign(drain_policy_signature_payload(policy))
    policy = replace(policy, approval_signature=base64.b64encode(signature).decode("ascii"))
    try:
        with factory() as session:
            session.add(
                ConversationModel(
                    conversation_id="postgres-old-v1",
                    actor_id=uuid4(),
                    community_id=uuid4(),
                    status="ACTIVE",
                    runtime_version="v1",
                    created_at=now - timedelta(days=60),
                    updated_at=now - timedelta(days=40),
                    last_message_at=now - timedelta(days=40),
                )
            )
            session.commit()
            inventory = build_v1_drain_inventory(
                session,
                release_sha="a" * 40,
                now=now,
                abandoned_after=timedelta(days=30),
            )
            receipt = expire_abandoned_v1(
                session,
                policy=policy,
                approval_authority=authority,
                now=now,
                dry_run=False,
            )
            session.commit()
            row = (
                session.query(ConversationModel).filter_by(conversation_id="postgres-old-v1").one()
            )
        assert inventory.database_snapshot != "non-postgresql-test-snapshot"
        assert receipt.expired == 1
        assert row.v1_drain_state == "EXPIRED"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
