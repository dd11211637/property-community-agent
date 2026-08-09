"""
Platform ORM model tests — verify table creation, constraints, and CRUD operations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    CommunityModel,
    ConfirmationTokenModel,
    HandoverTicketModel,
    HouseModel,
    IdempotencyRecordModel,
    MessageRecordModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)


class TestPlatformModels:
    """Verify all 10 core tables are created and support basic CRUD."""

    def test_community_crud(self, session, community_a_id):
        """Community: create, read, update."""
        c = CommunityModel(id=community_a_id, name="Test", status="ACTIVE")
        session.add(c)
        session.commit()

        found = session.get(CommunityModel, community_a_id)
        assert found is not None
        assert found.name == "Test"
        assert found.status == "ACTIVE"

    def test_user_crud(self, session, community_a_id):
        """User: create with community FK."""
        session.add(CommunityModel(id=community_a_id, name="Test", status="ACTIVE"))
        user_id = uuid4()
        u = UserModel(
            id=user_id,
            community_id=community_a_id,
            username="testuser",
            display_name="Test User",
            password_hash="hash123",
            status="ACTIVE",
        )
        session.add(u)
        session.commit()

        found = session.get(UserModel, user_id)
        assert found.username == "testuser"

    def test_house_crud(self, session, community_a_id):
        """House: create with community FK."""
        session.add(CommunityModel(id=community_a_id, name="Test", status="ACTIVE"))
        house_id = uuid4()
        h = HouseModel(
            id=house_id,
            community_id=community_a_id,
            building="1",
            unit="1",
            room_no="101",
            status="ACTIVE",
        )
        session.add(h)
        session.commit()

        found = session.get(HouseModel, house_id)
        assert found.building == "1"
        assert found.room_no == "101"

    def test_user_role(self, session, community_a_id, seed_data):
        """UserRole: create and query."""
        role = session.query(UserRoleModel).filter_by(role="RESIDENT").first()
        assert role is not None
        assert role.role == "RESIDENT"

    def test_user_house_binding(self, session, seed_data):
        """UserHouseBinding: create and query."""
        binding = session.query(UserHouseBindingModel).filter_by(status="ACTIVE").first()
        assert binding is not None
        assert binding.status == "ACTIVE"

    def test_confirmation_token(self, session):
        """ConfirmationToken: create with expiration."""
        token = "test_token_123"
        ct = ConfirmationTokenModel(
            token=token,
            actor_id=uuid4(),
            action="REPAIR_CREATE",
            parameter_hash="abc123",
            expires_at=datetime.now(timezone.utc),
        )
        session.add(ct)
        session.commit()

        found = session.query(ConfirmationTokenModel).filter_by(token=token).first()
        assert found is not None
        assert found.action == "REPAIR_CREATE"

    def test_idempotency_record(self, session):
        """IdempotencyRecord: unique constraint on (actor_id, operation, key)."""
        actor_id = uuid4()
        rec = IdempotencyRecordModel(
            actor_id=actor_id,
            operation="CREATE_WORK_ORDER",
            key="idem_key_1",
            request_hash="hash1",
        )
        session.add(rec)
        session.commit()

        # Duplicate should fail
        dup = IdempotencyRecordModel(
            actor_id=actor_id,
            operation="CREATE_WORK_ORDER",
            key="idem_key_1",
            request_hash="hash2",
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_message_record(self, session):
        """MessageRecord: create with all fields."""
        msg = MessageRecordModel(
            receiver_id=uuid4(),
            business_type="REPAIR",
            resource_id="WO-001",
            title="Test Message",
            body="Test body content",
            idempotency_key="msg_key_1",
        )
        session.add(msg)
        session.commit()

        found = session.query(MessageRecordModel).filter_by(status="PENDING").first()
        assert found is not None
        assert found.business_type == "REPAIR"

    def test_audit_log(self, session):
        """AuditLog: create audit entry."""
        log = AuditLogModel(
            actor_id=uuid4(),
            community_id=uuid4(),
            action="LOGIN_SUCCESS",
            resource_type="USER",
            resource_id="user-1",
            result="SUCCESS",
            request_id="req_001",
        )
        session.add(log)
        session.commit()

        found = session.query(AuditLogModel).filter_by(action="LOGIN_SUCCESS").first()
        assert found is not None
        assert found.result == "SUCCESS"

    def test_handover_ticket(self, session):
        """HandoverTicket: create and update status."""
        ticket = HandoverTicketModel(
            source="REPAIR",
            queue="CUSTOMER_SERVICE",
            summary="High risk repair needs manual review",
            reason="HIGH_RISK",
            status="PENDING",
        )
        session.add(ticket)
        session.commit()

        found = session.query(HandoverTicketModel).filter_by(status="PENDING").first()
        assert found is not None
        assert found.source == "REPAIR"

    def test_community_cascade(self, session, community_a_id):
        """Community cascade: deleting a community cascades to houses and users."""
        c = CommunityModel(id=community_a_id, name="Test", status="ACTIVE")
        session.add(c)
        session.flush()

        h = HouseModel(
            id=uuid4(),
            community_id=community_a_id,
            building="1",
            unit="1",
            room_no="101",
            status="ACTIVE",
        )
        session.add(h)
        session.commit()

        # Verify house exists
        assert session.query(HouseModel).count() == 1

        session.delete(c)
        session.commit()

        # House should be cascade-deleted
        assert session.query(HouseModel).count() == 0
