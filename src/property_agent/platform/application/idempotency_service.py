"""
Application-layer idempotency service — PF-04.

Provides IdempotencyService with request hash computation, idempotency record
lookup, snapshot storage, and conflict detection.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.domain.exceptions import IdempotencyConflictException
from property_agent.platform.infrastructure.orm_models import IdempotencyRecordModel


def _hash_dict(data: dict[str, Any]) -> str:
    """Deterministic SHA-256 of a request body — see ``platform.application.hashing``."""
    return canonical_hash(data)


class IdempotencyService:
    """Validates Idempotency-Key. Replay returns cached snapshot; param mismatch returns 409.

    Usage::

        svc = IdempotencyService(db_session)
        cached = svc.check(actor_id=..., operation="CREATE_BILL", key=..., request_body=...)
        if cached is not None:
            return cached  # replay — return previous response snapshot
        # ... execute business logic ...
        svc.update_snapshot(actor_id=..., operation="CREATE_BILL", key=...,
                            resource_id=..., response_snapshot=...)
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def check(
        self,
        *,
        actor_id: UUID,
        operation: str,
        key: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Check if this idempotency key has been seen before.

        Returns:
            None if this is a new request (proceed with business logic).
            A dict with cached response_snapshot if replay (return cached response).

        Raises:
            IdempotencyConflictException: same key but different request parameters.
        """
        request_hash = _hash_dict(request_body)

        record = (
            self._session.query(IdempotencyRecordModel)
            .filter_by(actor_id=actor_id, operation=operation, key=key)
            .first()
        )

        if record is None:
            # Claim the key immediately inside a savepoint.  The database unique
            # constraint arbitrates concurrent requests; a losing transaction can
            # then replay the winner instead of leaking an IntegrityError.
            try:
                with self._session.begin_nested():
                    self._session.add(
                        IdempotencyRecordModel(
                            actor_id=actor_id,
                            operation=operation,
                            key=key,
                            request_hash=request_hash,
                        )
                    )
                    self._session.flush()
                return None
            except IntegrityError:
                record = (
                    self._session.query(IdempotencyRecordModel)
                    .filter_by(actor_id=actor_id, operation=operation, key=key)
                    .first()
                )
                if record is None:
                    raise

        if record.request_hash != request_hash:
            raise IdempotencyConflictException(
                actor_id=str(actor_id),
                operation=operation,
                key=key,
            )

        # Same key, same hash — replay, return cached snapshot
        return record.response_snapshot

    def update_snapshot(
        self,
        *,
        actor_id: UUID,
        operation: str,
        key: str,
        resource_id: str,
        response_snapshot: dict[str, Any],
    ) -> None:
        """Update the idempotency record with the actual response after successful processing."""
        # Production sessions use ``autoflush=False``. Flush the new record
        # created by ``check`` so this query can update its response snapshot
        # in the same transaction; otherwise the first replay executes twice.
        self._session.flush()
        record = (
            self._session.query(IdempotencyRecordModel)
            .filter_by(actor_id=actor_id, operation=operation, key=key)
            .first()
        )
        if record:
            record.resource_id = resource_id
            record.response_snapshot = response_snapshot
