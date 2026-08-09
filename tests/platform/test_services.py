"""
Platform services tests — idempotency, confirmation, audit, and message outbox.

PRD 5.3: PF-04, PF-05, PF-06.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from property_agent.platform.services.errors import (
    ConfirmationError,
    IdempotencyConflict,
)
from property_agent.platform.services.shared import (
    AuditService,
    ConfirmationService,
    IdempotencyService,
    MessageOutboxService,
    _hash_dict,
    _mask_sensitive,
)


class TestIdempotencyService:
    """PF-04: Idempotency key validation."""

    def test_new_request_returns_none(self, session):
        svc = IdempotencyService(session)
        result = svc.check(
            actor_id=uuid4(),
            operation="CREATE_WORK_ORDER",
            key="key_001",
            request_body={"type": "repair"},
        )
        assert result is None  # New request, proceed

    def test_replay_returns_cached_snapshot(self, session):
        actor_id = uuid4()
        svc = IdempotencyService(session)

        # First request
        svc.check(
            actor_id=actor_id,
            operation="CREATE_WORK_ORDER",
            key="key_001",
            request_body={"type": "repair"},
        )
        svc.update_snapshot(
            actor_id=actor_id,
            operation="CREATE_WORK_ORDER",
            key="key_001",
            resource_id="WO-123",
            response_snapshot={"status": "created", "id": "WO-123"},
        )
        session.commit()

        # Replay with same params
        result = svc.check(
            actor_id=actor_id,
            operation="CREATE_WORK_ORDER",
            key="key_001",
            request_body={"type": "repair"},
        )
        assert result is not None
        assert result["id"] == "WO-123"

    def test_param_conflict_raises_409(self, session):
        actor_id = uuid4()
        svc = IdempotencyService(session)

        svc.check(
            actor_id=actor_id,
            operation="CREATE_WORK_ORDER",
            key="key_001",
            request_body={"type": "repair"},
        )
        session.commit()

        with pytest.raises(IdempotencyConflict) as exc:
            svc.check(
                actor_id=actor_id,
                operation="CREATE_WORK_ORDER",
                key="key_001",
                request_body={"type": "inspection"},  # Different params!
            )
        assert exc.value.status_code == 409


class TestConfirmationService:
    """PF-04: Confirmation token generation and validation."""

    def test_generate_and_consume(self, session):
        actor_id = uuid4()
        svc = ConfirmationService(session)

        params = {"house_id": "h1", "type": "repair"}
        token = svc.generate(actor_id=actor_id, action="REPAIR_CREATE", parameters=params)
        assert token is not None
        assert len(token) > 0

        session.commit()

        # Consume should succeed
        svc.consume(
            token=token,
            actor_id=actor_id,
            action="REPAIR_CREATE",
            parameter_hash=_hash_dict(params),
            request_id="req_001",
        )

    def test_consume_twice_fails(self, session):
        actor_id = uuid4()
        svc = ConfirmationService(session)

        params = {"house_id": "h1"}
        token = svc.generate(actor_id=actor_id, action="REPAIR_CREATE", parameters=params)
        session.commit()

        svc.consume(
            token=token,
            actor_id=actor_id,
            action="REPAIR_CREATE",
            parameter_hash=_hash_dict(params),
            request_id="req_001",
        )

        with pytest.raises(ConfirmationError) as exc:
            svc.consume(
                token=token,
                actor_id=actor_id,
                action="REPAIR_CREATE",
                parameter_hash=_hash_dict(params),
                request_id="req_002",
            )
        assert "already been used" in exc.value.message.lower()

    def test_expired_token_fails(self, session):
        actor_id = uuid4()
        svc = ConfirmationService(session)

        params = {"house_id": "h1"}
        token = svc.generate(actor_id=actor_id, action="REPAIR_CREATE", parameters=params)

        # Manually expire the token
        from property_agent.platform.infrastructure.orm_models import ConfirmationTokenModel

        rec = session.query(ConfirmationTokenModel).filter_by(token=token).first()
        rec.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.commit()

        with pytest.raises(ConfirmationError) as exc:
            svc.consume(
                token=token,
                actor_id=actor_id,
                action="REPAIR_CREATE",
                parameter_hash=_hash_dict(params),
                request_id="req_001",
            )
        assert "expired" in exc.value.message.lower()

    def test_actor_mismatch_fails(self, session):
        svc = ConfirmationService(session)
        token = svc.generate(actor_id=uuid4(), action="REPAIR_CREATE", parameters={"x": "1"})
        session.commit()

        with pytest.raises(ConfirmationError) as exc:
            svc.consume(
                token=token,
                actor_id=uuid4(),  # Different actor!
                action="REPAIR_CREATE",
                parameter_hash=_hash_dict({"x": "1"}),
                request_id="req_001",
            )
        assert "actor" in exc.value.message.lower()

    def test_param_hash_mismatch_fails(self, session):
        actor_id = uuid4()
        svc = ConfirmationService(session)
        token = svc.generate(actor_id=actor_id, action="REPAIR_CREATE", parameters={"x": "1"})
        session.commit()

        with pytest.raises(ConfirmationError) as exc:
            svc.consume(
                token=token,
                actor_id=actor_id,
                action="REPAIR_CREATE",
                parameter_hash=_hash_dict({"x": "2"}),  # Different params!
                request_id="req_001",
            )
        assert "param" in exc.value.message.lower() or "changed" in exc.value.message.lower()


class TestAuditService:
    """PF-06: Audit log writing with sensitive field masking."""

    def test_log_writes_entry(self, session):
        svc = AuditService(session)
        svc.log(
            actor_id=uuid4(),
            community_id=uuid4(),
            action="LOGIN_SUCCESS",
            resource_type="USER",
            resource_id="user-1",
            parameter_summary={"username": "test", "ip": "192.168.1.1"},
            result="SUCCESS",
            request_id="req_001",
        )
        session.commit()

        from property_agent.platform.infrastructure.orm_models import AuditLogModel

        logs = session.query(AuditLogModel).all()
        assert len(logs) == 1
        assert logs[0].action == "LOGIN_SUCCESS"

    def test_sensitive_fields_are_masked(self, session):
        svc = AuditService(session)
        svc.log(
            actor_id=uuid4(),
            community_id=uuid4(),
            action="LOGIN_SUCCESS",
            resource_type="USER",
            parameter_summary={"username": "test", "password": "secret123", "phone": "13800138000"},
            result="SUCCESS",
            request_id="req_001",
        )
        session.commit()

        from property_agent.platform.infrastructure.orm_models import AuditLogModel

        log = session.query(AuditLogModel).first()
        assert log.parameter_summary["password"] == "***REDACTED***"
        assert log.parameter_summary["phone"] == "138****8000"  # PF-06: regex masking
        assert log.parameter_summary["username"] == "test"


class TestMessageOutboxService:
    """PF-05: Message outbox with deduplication and retry."""

    def test_enqueue_creates_message(self, session):
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=uuid4(),
            business_type="REPAIR",
            resource_id="WO-001",
            title="Work Order Created",
            body="Your work order WO-001 has been created.",
            idempotency_key="msg_key_001",
        )
        session.commit()

        assert msg_id is not None

        from property_agent.platform.infrastructure.orm_models import MessageRecordModel

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.status == "PENDING"
        assert msg.retry_count == 0

    def test_enqueue_deduplicates(self, session):
        svc = MessageOutboxService(session)
        id1 = svc.enqueue(
            receiver_id=uuid4(),
            business_type="REPAIR",
            resource_id="WO-001",
            title="Title",
            body="Body",
            idempotency_key="dedup_key",
        )
        session.commit()

        id2 = svc.enqueue(
            receiver_id=uuid4(),
            business_type="REPAIR",
            resource_id="WO-001",
            title="Title",
            body="Body",
            idempotency_key="dedup_key",  # Same key!
        )
        assert id1 == id2

    def test_mark_sent(self, session):
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=uuid4(),
            business_type="REPAIR",
            resource_id="WO-001",
            title="Title",
            body="Body",
            idempotency_key="sent_key",
        )
        session.commit()

        svc.mark_sent(msg_id)
        session.commit()

        from property_agent.platform.infrastructure.orm_models import MessageRecordModel

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.status == "SENT"

    def test_mark_failed_and_retry(self, session):
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=uuid4(),
            business_type="REPAIR",
            resource_id="WO-001",
            title="Title",
            body="Body",
            idempotency_key="fail_key",
        )
        session.commit()

        # Retry up to MAX_RETRY_COUNT (5) times, should stay PENDING first 4 times
        svc.mark_failed(msg_id, "Network error")
        session.commit()
        from property_agent.platform.infrastructure.orm_models import MessageRecordModel

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 1
        assert msg.status == "PENDING"

        svc.mark_failed(msg_id, "Network error")
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 2
        assert msg.status == "PENDING"

        svc.mark_failed(msg_id, "Network error")
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 3
        assert msg.status == "PENDING"

        svc.mark_failed(msg_id, "Network error")
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 4
        assert msg.status == "PENDING"

        # 5th retry should mark as FAILED
        svc.mark_failed(msg_id, "Network error")
        session.commit()
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 5
        assert msg.status == "FAILED"

    def test_mark_read(self, session):
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=uuid4(),
            business_type="REPAIR",
            resource_id="WO-001",
            title="Title",
            body="Body",
            idempotency_key="read_key",
        )
        session.commit()

        svc.mark_read(msg_id)
        session.commit()

        from property_agent.platform.infrastructure.orm_models import MessageRecordModel

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.status == "READ"


class TestHelpers:
    def test_hash_dict_deterministic(self):
        h1 = _hash_dict({"a": 1, "b": 2})
        h2 = _hash_dict({"b": 2, "a": 1})
        assert h1 == h2  # Order-independent

    def test_hash_dict_different(self):
        h1 = _hash_dict({"a": 1})
        h2 = _hash_dict({"a": 2})
        assert h1 != h2

    def test_mask_sensitive(self):
        data = {
            "username": "test",
            "password": "secret",
            "phone": "13800138000",
            "address": "Beijing",
        }
        masked = _mask_sensitive(data)
        assert masked["password"] == "***REDACTED***"
        assert masked["phone"] == "138****8000"  # PF-06: regex masking, not full redaction
        assert masked["username"] == "test"
        assert masked["address"] == "Beijing"
