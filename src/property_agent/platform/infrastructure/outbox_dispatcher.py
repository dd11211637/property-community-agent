"""
Infrastructure-layer OutboxDispatcher — PF-05.

Async background task that polls the MessageRecord table for PENDING messages
and dispatches them with exponential backoff retry.

Delivery state flow: PENDING → SENT / FAILED. Read state is stored separately.
Max retries: 5 (after which status stays FAILED for management visibility)
Backoff: 2^retry_count * 2 seconds
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from property_agent.platform.infrastructure.orm_models import (
    HandoverTicketModel,
    MessageRecordModel,
    UserModel,
)

logger = logging.getLogger(__name__)

MAX_RETRY_COUNT = 5
POLL_INTERVAL_SECONDS = 5
BATCH_SIZE = 50


class OutboxDispatcher:
    """Async background dispatcher for message outbox.

    Polls the MessageRecord table on a configurable interval, attempts to
    deliver each PENDING message via a pluggable ``send_message`` callback,
    and applies exponential backoff on failure.

    Usage::

        dispatcher = OutboxDispatcher(
            session_factory=sessionmaker(bind=engine),
            send_message=my_send_function,
        )
        task = asyncio.create_task(dispatcher.run())
        # ... application runs ...
        await dispatcher.stop()
        await task
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        send_message: Callable[[MessageRecordModel], Awaitable[bool]],
        poll_interval: float = POLL_INTERVAL_SECONDS,
        batch_size: int = BATCH_SIZE,
        max_retry: int = MAX_RETRY_COUNT,
    ) -> None:
        self._session_factory = session_factory
        self._send_message = send_message
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._max_retry = max_retry
        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Start the polling loop. Runs until stop() is called."""
        self._running = True
        self._stop_event.clear()
        logger.info(
            "OutboxDispatcher started (poll=%ss, batch=%s, max_retry=%s)",
            self._poll_interval,
            self._batch_size,
            self._max_retry,
        )

        while self._running:
            try:
                await self._process_batch(respect_backoff=True)
            except Exception:
                logger.exception("OutboxDispatcher: unhandled error in poll cycle")

            # Wait before next poll
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except TimeoutError:
                pass

        logger.info("OutboxDispatcher stopped")

    async def stop(self) -> None:
        """Signal the dispatcher to stop after the current poll cycle."""
        self._running = False
        self._stop_event.set()

    async def run_once(self, *, respect_backoff: bool = False) -> int:
        """Process one batch of pending messages (useful for testing).

        Manual runs force a retry by default. The recurring production loop passes
        ``respect_backoff=True`` so a failed record is not retried before its delay.

        Returns the number of messages processed.
        """
        return await self._process_batch(respect_backoff=respect_backoff)

    # -- internal --

    async def _process_batch(self, *, respect_backoff: bool = False) -> int:
        """Fetch and dispatch one batch of pending messages."""
        session = self._session_factory()
        try:
            messages = (
                session.query(MessageRecordModel)
                .filter_by(status="PENDING")
                .order_by(MessageRecordModel.created_at)
                .with_for_update(skip_locked=True)
                .limit(self._batch_size)
                .all()
            )

            if respect_backoff:
                now = datetime.now(timezone.utc)
                messages = [message for message in messages if self._retry_due(message, now)]
            if not messages:
                return 0

            processed = 0
            for msg in messages:
                try:
                    success = await self._send_message(msg)
                    if success:
                        self._mark_sent(session, msg)
                    else:
                        self._mark_retry(session, msg, "send_message returned False")
                except Exception:
                    self._mark_retry(session, msg, traceback.format_exc())
                processed += 1

            session.commit()
            return processed

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _mark_sent(self, session: Session, msg: MessageRecordModel) -> None:
        """Mark a message as successfully sent."""
        msg.status = "SENT"
        msg.updated_at = datetime.now(timezone.utc)

    def _mark_retry(self, session: Session, msg: MessageRecordModel, error: str) -> None:
        """Increment retry count and apply exponential backoff logic.

        On reaching max_retry, status is set to FAILED for management visibility.
        Otherwise, remains PENDING with incremented retry_count.
        """
        msg.retry_count += 1
        msg.last_error = error
        msg.updated_at = datetime.now(timezone.utc)

        if msg.retry_count >= self._max_retry:
            msg.status = "FAILED"
            self._ensure_manual_handover(session, msg)
            logger.warning(
                "OutboxDispatcher: message %s exceeded max retries (%s/%s)",
                msg.id,
                msg.retry_count,
                self._max_retry,
            )
        else:
            # Exponential backoff is applied by checking retry_count
            # before next dispatch — the dispatcher only picks up PENDING
            # messages, and the backoff delay is calculated in
            # _should_retry_now based on updated_at + backoff_delay
            backoff_seconds = 2**msg.retry_count * 2
            logger.info(
                "OutboxDispatcher: message %s retry %s/%s, backoff=%ss",
                msg.id,
                msg.retry_count,
                self._max_retry,
                backoff_seconds,
            )

    @staticmethod
    def _ensure_manual_handover(session: Session, msg: MessageRecordModel) -> None:
        """Create one visible manual-takeover ticket after retry exhaustion."""
        existing = (
            session.query(HandoverTicketModel.id)
            .filter_by(resource_type="MESSAGE", resource_id=str(msg.id))
            .first()
        )
        if existing:
            return
        receiver = session.get(UserModel, msg.receiver_id)
        if receiver is None:
            logger.error("Cannot create message handover: receiver %s is missing", msg.receiver_id)
            return
        queue_by_business = {
            "REPAIR": "REPAIR",
            "INSPECTION": "SECURITY",
            "ANNOUNCEMENT": "MANAGEMENT",
            "BILLING": "CUSTOMER_SERVICE",
        }
        session.add(
            HandoverTicketModel(
                community_id=receiver.community_id,
                requester_id=msg.receiver_id,
                resource_type="MESSAGE",
                resource_id=str(msg.id),
                source=msg.business_type,
                queue=queue_by_business.get(msg.business_type, "CUSTOMER_SERVICE"),
                summary=f"消息投递达到重试上限：{msg.title}",
                reason="MESSAGE_DELIVERY_FAILED",
                status="PENDING",
                payload={"retry_count": msg.retry_count, "last_error": msg.last_error},
            )
        )

    @staticmethod
    def get_backoff_delay(retry_count: int) -> float:
        """Calculate exponential backoff delay: 2^retry_count * 2 seconds.

        Examples:
            retry_count=0 → 2^0 * 2 = 2s
            retry_count=1 → 2^1 * 2 = 4s
            retry_count=2 → 2^2 * 2 = 8s
            retry_count=3 → 2^3 * 2 = 16s
            retry_count=4 → 2^4 * 2 = 32s
        """
        return 2**retry_count * 2

    @classmethod
    def _retry_due(cls, message: MessageRecordModel, now: datetime) -> bool:
        if message.retry_count <= 0:
            return True
        updated_at = message.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        delay = timedelta(seconds=cls.get_backoff_delay(message.retry_count))
        return updated_at + delay <= now


# ---------------------------------------------------------------------------
# MessageOutboxService — PF-05 message enqueue and status management
# ---------------------------------------------------------------------------


class MessageOutboxService:
    """Writes messages to the outbox (MessageRecord) for async delivery.

    Used by business logic to enqueue notification messages. The actual
    delivery is handled by OutboxDispatcher asynchronously.

    Usage::

        svc = MessageOutboxService(db_session)
        msg_id = svc.enqueue(
            receiver_id=..., business_type="REPAIR", resource_id=...,
            title="New repair assigned", body="...", idempotency_key="..."
        )
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        receiver_id: UUID,
        business_type: str,
        resource_id: str,
        title: str,
        body: str,
        idempotency_key: str,
    ) -> UUID:
        """Write a message to the outbox with deduplication.

        If a message with the same idempotency_key already exists, returns
        the existing message ID instead of creating a duplicate.
        """
        storage_key = idempotency_key
        if len(storage_key) > 128:
            storage_key = f"sha256:{hashlib.sha256(storage_key.encode('utf-8')).hexdigest()}"
        existing = (
            self._session.query(MessageRecordModel).filter_by(idempotency_key=storage_key).first()
        )
        if existing:
            return existing.id

        msg = MessageRecordModel(
            receiver_id=receiver_id,
            business_type=business_type,
            resource_id=resource_id,
            title=title,
            body=body,
            idempotency_key=storage_key,
        )
        self._session.add(msg)
        self._session.flush()
        return msg.id

    def mark_sent(self, message_id: UUID) -> None:
        """Mark a message as successfully sent."""
        msg = self._session.get(MessageRecordModel, message_id)
        if msg:
            msg.status = "SENT"
            msg.updated_at = datetime.now(timezone.utc)

    def mark_failed(self, message_id: UUID, error: str) -> None:
        """Mark a message as failed and increment retry count."""
        msg = self._session.get(MessageRecordModel, message_id)
        if msg:
            msg.retry_count += 1
            msg.last_error = error
            msg.updated_at = datetime.now(timezone.utc)
            if msg.retry_count >= MAX_RETRY_COUNT:
                msg.status = "FAILED"
                OutboxDispatcher._ensure_manual_handover(self._session, msg)

    def mark_read(self, message_id: UUID) -> None:
        """Mark a message as read by the receiver."""
        msg = self._session.get(MessageRecordModel, message_id)
        if msg and msg.read_at is None:
            msg.read_at = datetime.now(timezone.utc)
            msg.updated_at = datetime.now(timezone.utc)

    def get_pending(self, limit: int = 50) -> list[MessageRecordModel]:
        """Get pending messages for the background sender."""
        return (
            self._session.query(MessageRecordModel)
            .filter_by(status="PENDING")
            .order_by(MessageRecordModel.created_at)
            .limit(limit)
            .all()
        )

    def get_failed_visible(self) -> list[MessageRecordModel]:
        """Get failed messages that should be visible in management console."""
        return (
            self._session.query(MessageRecordModel)
            .filter_by(status="FAILED")
            .order_by(MessageRecordModel.updated_at.desc())
            .limit(100)
            .all()
        )
