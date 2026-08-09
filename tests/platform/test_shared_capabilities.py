"""
Tests for platform shared capabilities — PF-04, PF-05, PF-06.

Covers:
- Idempotency: same key+params → replay snapshot; diff params → 409
- ConfirmationToken: param modification/timeout → 400 INVALID_CONFIRMATION_TOKEN
- Message Outbox: retry_count increments on failure, status tracks correctly
- Audit log: correct actor_id/action, phone masking 138****1234
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from property_agent.platform.adapters.api.dependencies import (
    RequestContext,
    require_idempotency_key,
)
from property_agent.platform.application.audit_service import (
    AuditService,
    DataMasker,
    audit_log,
)
from property_agent.platform.application.confirmation_service import (
    ConfirmationService,
)
from property_agent.platform.application.idempotency_service import (
    IdempotencyService,
)
from property_agent.platform.domain.exceptions import (
    IdempotencyConflictException,
    InvalidConfirmationTokenException,
)
from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    Base,
    CommunityModel,
    MessageRecordModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import (
    MAX_RETRY_COUNT,
    MessageOutboxService,
    OutboxDispatcher,
)

# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def engine():
    """In-memory SQLite engine."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Fresh session per test."""
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.rollback()
    sess.close()


@pytest.fixture
def actor_id() -> UUID:
    return UUID("e0000000-0000-0000-0000-000000000001")


@pytest.fixture
def community_id() -> UUID:
    return UUID("c0000000-0000-0000-0000-000000000001")


@pytest.fixture
def request_context(actor_id, community_id):
    """Create a basic RequestContext for testing."""
    return RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=frozenset({"RESIDENT"}),
        request_id="test-request-001",
    )


# ═══════════════════════════════════════════════════════════════
# PF-04: Idempotency Tests
# ═══════════════════════════════════════════════════════════════


class TestIdempotencyService:
    """Tests for IdempotencyService.check() and update_snapshot()."""

    def test_new_request_returns_none(self, session, actor_id):
        """First request with a new key should return None (proceed)."""
        svc = IdempotencyService(session)
        result = svc.check(
            actor_id=actor_id,
            operation="CREATE_BILL",
            key="idem-key-001",
            request_body={"amount": 100, "house_id": "h1"},
        )
        session.commit()

        assert result is None

    def test_same_key_same_params_returns_snapshot(self, session, actor_id):
        """Same key + same params should return the cached response snapshot."""
        svc = IdempotencyService(session)
        body = {"amount": 100, "house_id": "h1"}

        # First request — proceed
        result1 = svc.check(
            actor_id=actor_id,
            operation="CREATE_BILL",
            key="idem-key-002",
            request_body=body,
        )
        assert result1 is None

        # Save snapshot
        svc.update_snapshot(
            actor_id=actor_id,
            operation="CREATE_BILL",
            key="idem-key-002",
            resource_id="bill-001",
            response_snapshot={"id": "bill-001", "status": "created"},
        )
        session.commit()

        # Second request (replay) — should return cached snapshot
        result2 = svc.check(
            actor_id=actor_id,
            operation="CREATE_BILL",
            key="idem-key-002",
            request_body=body,
        )
        assert result2 is not None
        assert result2["id"] == "bill-001"
        assert result2["status"] == "created"

    def test_same_key_different_params_raises_409(self, session, actor_id):
        """Same key + different params should raise IdempotencyConflictException (409)."""
        svc = IdempotencyService(session)

        # First request
        svc.check(
            actor_id=actor_id,
            operation="CREATE_BILL",
            key="idem-key-003",
            request_body={"amount": 100},
        )
        session.commit()

        # Second request with different body
        with pytest.raises(IdempotencyConflictException) as exc:
            svc.check(
                actor_id=actor_id,
                operation="CREATE_BILL",
                key="idem-key-003",
                request_body={"amount": 200},
            )

        assert exc.value.code == "IDEMPOTENCY_CONFLICT"
        assert exc.value.status_code == 409

    def test_different_operations_dont_conflict(self, session, actor_id):
        """Same key but different operations should not conflict."""
        svc = IdempotencyService(session)
        body = {"amount": 100}

        result1 = svc.check(
            actor_id=actor_id,
            operation="CREATE_BILL",
            key="key-001",
            request_body=body,
        )
        session.commit()
        assert result1 is None

        result2 = svc.check(
            actor_id=actor_id,
            operation="DELETE_BILL",
            key="key-001",
            request_body=body,
        )
        session.commit()
        assert result2 is None  # Different operation, should be treated as new

    def test_different_actors_dont_conflict(self, session, actor_id):
        """Same key but different actors should not conflict."""
        svc = IdempotencyService(session)
        body = {"amount": 100}
        other_actor = UUID("e0000000-0000-0000-0000-000000000099")

        result1 = svc.check(
            actor_id=actor_id,
            operation="CREATE_BILL",
            key="key-001",
            request_body=body,
        )
        session.commit()
        assert result1 is None

        result2 = svc.check(
            actor_id=other_actor,
            operation="CREATE_BILL",
            key="key-001",
            request_body=body,
        )
        session.commit()
        assert result2 is None  # Different actor, should be treated as new


# ═══════════════════════════════════════════════════════════════
# PF-04: require_idempotency_key dependency test
# ═══════════════════════════════════════════════════════════════


class TestRequireIdempotencyKey:
    """Tests for the require_idempotency_key FastAPI dependency."""

    @pytest.fixture
    def app_with_idempotency(self):
        """Minimal FastAPI app with require_idempotency_key dependency."""
        app = FastAPI()

        @app.post("/write")
        async def write_endpoint(
            key: str = Depends(require_idempotency_key),
        ):
            return {"key": key}

        @app.get("/read")
        async def read_endpoint():
            return {"ok": True}

        return app

    @pytest.fixture
    def client(self, app_with_idempotency):
        return TestClient(app_with_idempotency)

    def test_missing_idempotency_key_returns_400(self, client):
        """Missing Idempotency-Key header should return 400."""
        response = client.post("/write", json={"data": "test"})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    def test_empty_idempotency_key_returns_400(self, client):
        """Empty Idempotency-Key header should return 400."""
        response = client.post(
            "/write",
            json={"data": "test"},
            headers={"Idempotency-Key": "   "},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    def test_valid_idempotency_key_passes(self, client):
        """Valid Idempotency-Key header should pass through."""
        response = client.post(
            "/write",
            json={"data": "test"},
            headers={"Idempotency-Key": "valid-key-123"},
        )
        assert response.status_code == 200
        assert response.json()["key"] == "valid-key-123"

    def test_get_request_does_not_require_key(self, client):
        """GET requests should not require Idempotency-Key."""
        response = client.get("/read")
        assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════
# PF-04: ConfirmationToken Tests
# ═══════════════════════════════════════════════════════════════


class TestConfirmationService:
    """Tests for ConfirmationService.generate_token() and validate_and_consume_token()."""

    def test_generate_and_consume_success(self, session, actor_id):
        """Generate a token and successfully consume it with matching params."""
        svc = ConfirmationService(session)
        params = {"bill_id": "b001", "action": "delete"}

        token = svc.generate_token(actor_id=actor_id, action="DELETE_BILL", params=params)
        session.commit()

        # Should not raise
        svc.validate_and_consume_token(
            token=token,
            actor_id=actor_id,
            action="DELETE_BILL",
            params=params,
        )
        session.commit()

    def test_params_modified_raises_400(self, session, actor_id):
        """Modified params after token generation should raise InvalidConfirmationTokenException."""
        svc = ConfirmationService(session)
        original_params = {"bill_id": "b001", "action": "delete"}

        token = svc.generate_token(actor_id=actor_id, action="DELETE_BILL", params=original_params)
        session.commit()

        modified_params = {"bill_id": "b001", "action": "archive"}  # different action

        with pytest.raises(InvalidConfirmationTokenException) as exc:
            svc.validate_and_consume_token(
                token=token,
                actor_id=actor_id,
                action="DELETE_BILL",
                params=modified_params,
            )

        assert exc.value.code == "INVALID_CONFIRMATION_TOKEN"
        assert exc.value.status_code == 400
        assert "changed" in exc.value.message.lower()

    def test_token_expired_raises_400(self, session, actor_id):
        """Expired token should raise InvalidConfirmationTokenException."""
        svc = ConfirmationService(session)
        params = {"bill_id": "b001"}

        token = svc.generate_token(actor_id=actor_id, action="DELETE_BILL", params=params)
        session.commit()

        # Manually expire the token
        from property_agent.platform.infrastructure.orm_models import ConfirmationTokenModel

        record = session.query(ConfirmationTokenModel).filter_by(token=token).first()
        record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()

        with pytest.raises(InvalidConfirmationTokenException) as exc:
            svc.validate_and_consume_token(
                token=token,
                actor_id=actor_id,
                action="DELETE_BILL",
                params=params,
            )

        assert "expired" in exc.value.message.lower()

    def test_token_already_consumed_raises_400(self, session, actor_id):
        """Already consumed token should raise InvalidConfirmationTokenException."""
        svc = ConfirmationService(session)
        params = {"bill_id": "b001"}

        token = svc.generate_token(actor_id=actor_id, action="DELETE_BILL", params=params)
        session.commit()

        # First consume — succeeds
        svc.validate_and_consume_token(
            token=token,
            actor_id=actor_id,
            action="DELETE_BILL",
            params=params,
        )
        session.commit()

        # Second consume — should fail
        with pytest.raises(InvalidConfirmationTokenException) as exc:
            svc.validate_and_consume_token(
                token=token,
                actor_id=actor_id,
                action="DELETE_BILL",
                params=params,
            )

        assert "already been used" in exc.value.message.lower()

    def test_actor_mismatch_raises_400(self, session, actor_id):
        """Different actor_id should raise InvalidConfirmationTokenException."""
        svc = ConfirmationService(session)
        params = {"bill_id": "b001"}
        other_actor = UUID("e0000000-0000-0000-0000-000000000099")

        token = svc.generate_token(actor_id=actor_id, action="DELETE_BILL", params=params)
        session.commit()

        with pytest.raises(InvalidConfirmationTokenException) as exc:
            svc.validate_and_consume_token(
                token=token,
                actor_id=other_actor,
                action="DELETE_BILL",
                params=params,
            )

        assert "actor" in exc.value.message.lower()

    def test_action_mismatch_raises_400(self, session, actor_id):
        """Different action should raise InvalidConfirmationTokenException."""
        svc = ConfirmationService(session)
        params = {"bill_id": "b001"}

        token = svc.generate_token(actor_id=actor_id, action="DELETE_BILL", params=params)
        session.commit()

        with pytest.raises(InvalidConfirmationTokenException) as exc:
            svc.validate_and_consume_token(
                token=token,
                actor_id=actor_id,
                action="ARCHIVE_BILL",
                params=params,
            )

        assert "action" in exc.value.message.lower()

    def test_nonexistent_token_raises_400(self, session, actor_id):
        """Non-existent token should raise InvalidConfirmationTokenException."""
        svc = ConfirmationService(session)
        params = {"bill_id": "b001"}

        with pytest.raises(InvalidConfirmationTokenException) as exc:
            svc.validate_and_consume_token(
                token="nonexistent-token",
                actor_id=actor_id,
                action="DELETE_BILL",
                params=params,
            )

        assert "not found" in exc.value.message.lower()


# ═══════════════════════════════════════════════════════════════
# PF-05: Message Outbox Tests
# ═══════════════════════════════════════════════════════════════


class TestMessageOutboxService:
    """Tests for MessageOutboxService enqueue and status management."""

    def test_enqueue_creates_message(self, session, actor_id):
        """Enqueue should create a new message with PENDING status."""
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="REPAIR",
            resource_id="repair-001",
            title="New repair assigned",
            body="You have a new repair task.",
            idempotency_key="msg-key-001",
        )
        session.commit()

        assert msg_id is not None

        msg = session.get(MessageRecordModel, msg_id)
        assert msg is not None
        assert msg.status == "PENDING"
        assert msg.retry_count == 0
        assert msg.business_type == "REPAIR"
        assert msg.receiver_id == actor_id

    def test_enqueue_deduplicates_by_idempotency_key(self, session, actor_id):
        """Same idempotency_key should return existing message ID."""
        svc = MessageOutboxService(session)

        msg_id1 = svc.enqueue(
            receiver_id=actor_id,
            business_type="REPAIR",
            resource_id="repair-001",
            title="First",
            body="Body 1",
            idempotency_key="dup-key",
        )
        session.commit()

        msg_id2 = svc.enqueue(
            receiver_id=actor_id,
            business_type="REPAIR",
            resource_id="repair-001",
            title="Second",
            body="Body 2",
            idempotency_key="dup-key",
        )
        session.commit()

        assert msg_id1 == msg_id2

        # Verify only one record exists
        count = session.query(MessageRecordModel).filter_by(idempotency_key="dup-key").count()
        assert count == 1

    def test_mark_sent(self, session, actor_id):
        """mark_sent should update status to SENT."""
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="REPAIR",
            resource_id="r1",
            title="T",
            body="B",
            idempotency_key="k1",
        )
        session.commit()

        svc.mark_sent(msg_id)
        session.commit()

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.status == "SENT"

    def test_mark_failed_increments_retry_count(self, session, actor_id):
        """mark_failed should increment retry_count and save error."""
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="REPAIR",
            resource_id="r1",
            title="T",
            body="B",
            idempotency_key="k2",
        )
        session.commit()

        svc.mark_failed(msg_id, "Connection timeout")
        session.commit()

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 1
        assert msg.last_error == "Connection timeout"
        assert msg.status == "PENDING"  # Not yet FAILED (below max_retry)

    def test_mark_failed_exceeding_max_retry(self, session, actor_id):
        """When retry_count reaches MAX_RETRY_COUNT, status should become FAILED."""
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="REPAIR",
            resource_id="r1",
            title="T",
            body="B",
            idempotency_key="k3",
        )
        session.commit()

        # Fail repeatedly until max retries
        for i in range(MAX_RETRY_COUNT):
            svc.mark_failed(msg_id, f"Error attempt {i + 1}")
            session.commit()

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == MAX_RETRY_COUNT
        assert msg.status == "FAILED"
        assert "Error attempt" in msg.last_error

    def test_mark_read(self, session, actor_id):
        """mark_read should update status to READ."""
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="REPAIR",
            resource_id="r1",
            title="T",
            body="B",
            idempotency_key="k4",
        )
        session.commit()

        svc.mark_read(msg_id)
        session.commit()

        msg = session.get(MessageRecordModel, msg_id)
        assert msg.status == "READ"

    def test_get_pending(self, session, actor_id):
        """get_pending should return only PENDING messages."""
        svc = MessageOutboxService(session)

        id1 = svc.enqueue(
            receiver_id=actor_id,
            business_type="R",
            resource_id="r1",
            title="T1",
            body="B1",
            idempotency_key="p1",
        )
        id2 = svc.enqueue(
            receiver_id=actor_id,
            business_type="R",
            resource_id="r2",
            title="T2",
            body="B2",
            idempotency_key="p2",
        )
        session.commit()

        svc.mark_sent(id2)
        session.commit()

        pending = svc.get_pending()
        assert len(pending) == 1
        assert pending[0].id == id1

    def test_get_failed_visible(self, session, actor_id):
        """get_failed_visible should return only FAILED messages."""
        svc = MessageOutboxService(session)

        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="R",
            resource_id="r1",
            title="T",
            body="B",
            idempotency_key="f1",
        )
        session.commit()

        # Fail until max
        for i in range(MAX_RETRY_COUNT):
            svc.mark_failed(msg_id, f"Error {i}")
            session.commit()

        failed = svc.get_failed_visible()
        assert len(failed) == 1
        assert failed[0].status == "FAILED"


# ═══════════════════════════════════════════════════════════════
# PF-05: OutboxDispatcher Tests
# ═══════════════════════════════════════════════════════════════


class TestOutboxDispatcher:
    """Tests for OutboxDispatcher async polling and exponential backoff."""

    @pytest.fixture
    def session_factory(self, engine):
        return sessionmaker(bind=engine)

    def test_backoff_delay_calculation(self):
        """Verify exponential backoff formula: 2^retry_count * 2."""
        assert OutboxDispatcher.get_backoff_delay(0) == 2  # 2^0 * 2
        assert OutboxDispatcher.get_backoff_delay(1) == 4  # 2^1 * 2
        assert OutboxDispatcher.get_backoff_delay(2) == 8  # 2^2 * 2
        assert OutboxDispatcher.get_backoff_delay(3) == 16  # 2^3 * 2
        assert OutboxDispatcher.get_backoff_delay(4) == 32  # 2^4 * 2
        assert OutboxDispatcher.get_backoff_delay(5) == 64  # 2^5 * 2

    @pytest.mark.asyncio
    async def test_dispatcher_processes_pending_messages(self, session_factory, actor_id):
        """Dispatcher should pick up PENDING messages and mark them SENT."""
        # Seed a pending message
        session = session_factory()
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="TEST",
            resource_id="r1",
            title="Test",
            body="Test body",
            idempotency_key="disp-001",
        )
        session.commit()
        session.close()

        # Mock send_message that always succeeds
        mock_send = AsyncMock(return_value=True)

        dispatcher = OutboxDispatcher(
            session_factory=session_factory,
            send_message=mock_send,
            batch_size=10,
            poll_interval=0.1,
        )

        processed = await dispatcher.run_once()

        assert processed == 1
        mock_send.assert_called_once()

        # Verify message is now SENT
        session = session_factory()
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.status == "SENT"
        session.close()

    @pytest.mark.asyncio
    async def test_dispatcher_retries_on_failure(self, session_factory, actor_id):
        """On send failure, retry_count should increment and status should track."""
        session = session_factory()
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="TEST",
            resource_id="r1",
            title="Test",
            body="Test body",
            idempotency_key="disp-002",
        )
        session.commit()
        session.close()

        # Mock send_message that always fails
        mock_send = AsyncMock(return_value=False)

        dispatcher = OutboxDispatcher(
            session_factory=session_factory,
            send_message=mock_send,
            batch_size=10,
            poll_interval=0.1,
            max_retry=MAX_RETRY_COUNT,
        )

        # Run once — should fail and increment retry_count
        processed = await dispatcher.run_once()
        assert processed == 1

        session = session_factory()
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 1
        assert msg.status == "PENDING"  # Not yet failed
        assert msg.last_error is not None
        session.close()

    @pytest.mark.asyncio
    async def test_dispatcher_max_retries_reached(self, session_factory, actor_id):
        """After max_retry attempts, message should be marked FAILED."""
        session = session_factory()
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="TEST",
            resource_id="r1",
            title="Test",
            body="Test body",
            idempotency_key="disp-003",
        )
        session.commit()
        session.close()

        mock_send = AsyncMock(return_value=False)

        dispatcher = OutboxDispatcher(
            session_factory=session_factory,
            send_message=mock_send,
            batch_size=10,
            poll_interval=0.1,
            max_retry=MAX_RETRY_COUNT,
        )

        # Run multiple times until max retries
        for _ in range(MAX_RETRY_COUNT):
            await dispatcher.run_once()

        session = session_factory()
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == MAX_RETRY_COUNT
        assert msg.status == "FAILED"
        session.close()

    @pytest.mark.asyncio
    async def test_dispatcher_handles_send_exception(self, session_factory, actor_id):
        """On send exception, error should be captured in last_error."""
        session = session_factory()
        svc = MessageOutboxService(session)
        msg_id = svc.enqueue(
            receiver_id=actor_id,
            business_type="TEST",
            resource_id="r1",
            title="Test",
            body="Test body",
            idempotency_key="disp-004",
        )
        session.commit()
        session.close()

        mock_send = AsyncMock(side_effect=RuntimeError("Network error"))

        dispatcher = OutboxDispatcher(
            session_factory=session_factory,
            send_message=mock_send,
            batch_size=10,
            poll_interval=0.1,
        )

        processed = await dispatcher.run_once()
        assert processed == 1

        session = session_factory()
        msg = session.get(MessageRecordModel, msg_id)
        assert msg.retry_count == 1
        assert "Network error" in msg.last_error
        session.close()


# ═══════════════════════════════════════════════════════════════
# PF-06: DataMasker Tests
# ═══════════════════════════════════════════════════════════════


class TestDataMasker:
    """Tests for DataMasker phone masking, secret masking, and URL masking."""

    def test_mask_phone_standard(self):
        """Standard Chinese mobile number should be masked: 138****1234."""
        result = DataMasker.mask_phone("13812341234")
        assert result == "138****1234"

    def test_mask_phone_different_prefix(self):
        """Different mobile prefix should also be masked."""
        result = DataMasker.mask_phone("15987654321")
        assert result == "159****4321"

    def test_mask_phone_in_text(self):
        """Phone numbers embedded in text should be masked."""
        result = DataMasker.mask_phone("Contact: 13812341234 for support")
        assert "138****1234" in result
        assert "13812341234" not in result

    def test_mask_phone_landline_not_masked(self):
        """Non-mobile numbers should not be masked by phone regex."""
        result = DataMasker.mask_phone("010-12345678")
        assert result == "010-12345678"  # Landline not matched

    def test_mask_sensitive_data_phone_field(self):
        """Phone field in dictionary should be masked."""
        data = {
            "phone": "13812341234",
            "name": "Test User",
            "amount": 100,
        }
        result = DataMasker.mask_sensitive_data(data)
        assert result["phone"] == "138****1234"
        assert result["name"] == "Test User"
        assert result["amount"] == 100

    def test_mask_sensitive_data_password_field(self):
        """Password field should be fully redacted."""
        data = {"username": "admin", "password": "secret123"}
        result = DataMasker.mask_sensitive_data(data)
        assert result["password"] == "***REDACTED***"
        assert result["username"] == "admin"

    def test_mask_sensitive_data_secret_field(self):
        """Secret/token fields should be fully redacted."""
        data = {"api_secret": "sk-abc123xyz", "user_id": "u1"}
        result = DataMasker.mask_sensitive_data(data)
        assert result["api_secret"] == "***REDACTED***"
        assert result["user_id"] == "u1"

    def test_mask_sensitive_data_nested(self):
        """Nested dictionaries should be recursively masked."""
        data = {
            "user": {
                "phone": "13900001111",
                "profile": {"id_card": "110101199001011234"},
            },
            "meta": "info",
        }
        result = DataMasker.mask_sensitive_data(data)
        assert result["user"]["phone"] == "139****1111"
        assert result["user"]["profile"]["id_card"] == "***REDACTED***"
        assert result["meta"] == "info"

    def test_mask_sensitive_data_list(self):
        """Lists containing sensitive data should be masked."""
        data = {
            "contacts": [
                {"phone": "13811112222", "name": "Alice"},
                {"phone": "13933334444", "name": "Bob"},
            ],
        }
        result = DataMasker.mask_sensitive_data(data)
        assert result["contacts"][0]["phone"] == "138****2222"
        assert result["contacts"][1]["phone"] == "139****4444"

    def test_mask_sensitive_data_none(self):
        """None input should return None."""
        result = DataMasker.mask_sensitive_data(None)
        assert result is None

    def test_mask_secrets(self):
        """Bearer tokens and secrets should be masked."""
        result = DataMasker.mask_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.xyz")
        assert "***REDACTED***" in result
        assert "eyJhbGci" not in result

    def test_mask_attachment_urls(self):
        """Attachment URLs with tokens should have tokens masked."""
        result = DataMasker.mask_attachment_urls(
            "https://files.example.com/doc.pdf?token=abc123&signature=xyz789"
        )
        assert "token=***REDACTED***" in result
        assert "signature=***REDACTED***" in result


# ═══════════════════════════════════════════════════════════════
# PF-06: AuditService Tests
# ═══════════════════════════════════════════════════════════════


class TestAuditService:
    """Tests for AuditService.log() with sensitive data masking."""

    def test_log_writes_audit_entry(self, session, actor_id, community_id):
        """Log should create an AuditLog entry with correct fields."""
        svc = AuditService(session)
        svc.log(
            actor_id=actor_id,
            community_id=community_id,
            action="LOGIN_SUCCESS",
            resource_type="USER",
            resource_id=str(actor_id),
            parameter_summary={"username": "testuser", "ip": "192.168.1.1"},
            result="SUCCESS",
            request_id="req-001",
        )
        session.commit()

        logs = session.query(AuditLogModel).filter_by(action="LOGIN_SUCCESS").all()
        assert len(logs) == 1
        log = logs[0]
        assert log.actor_id == actor_id
        assert log.community_id == community_id
        assert log.action == "LOGIN_SUCCESS"
        assert log.resource_type == "USER"
        assert log.result == "SUCCESS"
        assert log.request_id == "req-001"

    def test_log_masks_phone_in_parameter_summary(self, session, actor_id, community_id):
        """Phone numbers in parameter_summary should be masked to 138****1234 format."""
        svc = AuditService(session)
        svc.log(
            actor_id=actor_id,
            community_id=community_id,
            action="BILL_QUERY",
            resource_type="BILL",
            parameter_summary={
                "phone": "13812341234",
                "bill_id": "b001",
                "amount": 100.50,
            },
            result="SUCCESS",
            request_id="req-002",
        )
        session.commit()

        log = session.query(AuditLogModel).filter_by(action="BILL_QUERY").first()
        assert log is not None
        assert log.parameter_summary["phone"] == "138****1234"
        assert log.parameter_summary["bill_id"] == "b001"
        assert log.parameter_summary["amount"] == 100.50

    def test_log_masks_password_in_parameter_summary(self, session, actor_id, community_id):
        """Password fields should be fully redacted in audit log."""
        svc = AuditService(session)
        svc.log(
            actor_id=actor_id,
            community_id=community_id,
            action="LOGIN_FAILED",
            resource_type="USER",
            parameter_summary={
                "username": "admin",
                "password": "supersecret",
                "attempt": 3,
            },
            result="FAILURE",
            request_id="req-003",
        )
        session.commit()

        log = session.query(AuditLogModel).filter_by(action="LOGIN_FAILED").first()
        assert log.parameter_summary["password"] == "***REDACTED***"
        assert log.parameter_summary["username"] == "admin"

    def test_log_without_parameter_summary(self, session, actor_id, community_id):
        """Log without parameter_summary should work fine."""
        svc = AuditService(session)
        svc.log(
            actor_id=actor_id,
            community_id=community_id,
            action="ACCESS_DENIED",
            resource_type="HOUSE",
            resource_id="h-unauthorized",
            result="DENIED",
            request_id="req-004",
        )
        session.commit()

        log = session.query(AuditLogModel).filter_by(action="ACCESS_DENIED").first()
        assert log is not None
        assert log.parameter_summary is None

    def test_log_multiple_entries(self, session, actor_id, community_id):
        """Multiple audit log entries should coexist."""
        svc = AuditService(session)

        actions = ["LOGIN_SUCCESS", "BILL_QUERY", "REPAIR_CREATE", "LOGOUT"]
        for i, action in enumerate(actions):
            svc.log(
                actor_id=actor_id,
                community_id=community_id,
                action=action,
                resource_type="TEST",
                request_id=f"req-{i}",
            )

        session.commit()

        count = session.query(AuditLogModel).filter_by(actor_id=actor_id).count()
        assert count == 4


# ═══════════════════════════════════════════════════════════════
# PF-06: @audit_log decorator integration test
# ═══════════════════════════════════════════════════════════════


class TestAuditLogDecorator:
    """Tests for the @audit_log decorator with RequestContext integration."""

    def test_decorator_audits_success(self, session, actor_id, community_id, request_context):
        """Successful decorated function should write SUCCESS audit log."""
        request_context.activate()

        CommunityModel.__table__.create(bind=session.get_bind(), checkfirst=True)
        AuditLogModel.__table__.create(bind=session.get_bind(), checkfirst=True)

        @audit_log(action="TEST_ACTION", resource_type="TEST_RESOURCE")
        def test_func(db: Session, resource_id: str, value: int):
            return {"id": resource_id, "result": "ok"}

        result = test_func(db=session, resource_id="res-001", value=42)
        session.commit()

        assert result["id"] == "res-001"

        logs = session.query(AuditLogModel).filter_by(action="TEST_ACTION").all()
        assert len(logs) == 1
        log = logs[0]
        assert log.actor_id == actor_id
        assert log.community_id == community_id
        assert log.result == "SUCCESS"
        assert log.resource_id == "res-001"

    def test_decorator_audits_failure(self, session, actor_id, community_id, request_context):
        """Failed decorated function should write FAILURE audit log."""
        request_context.activate()

        CommunityModel.__table__.create(bind=session.get_bind(), checkfirst=True)
        AuditLogModel.__table__.create(bind=session.get_bind(), checkfirst=True)

        @audit_log(action="FAILING_ACTION", resource_type="TEST")
        def failing_func(db: Session, resource_id: str):
            raise ValueError("Something went wrong")

        with pytest.raises(ValueError):
            failing_func(db=session, resource_id="res-fail")

        session.commit()

        logs = session.query(AuditLogModel).filter_by(action="FAILING_ACTION").all()
        assert len(logs) == 1
        log = logs[0]
        assert log.result == "FAILURE"
        assert log.resource_id == "res-fail"
