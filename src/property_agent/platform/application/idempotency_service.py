"""
Application-layer idempotency service — PF-04.

Provides IdempotencyService with request hash computation, idempotency record
lookup, snapshot storage, and conflict detection.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from property_agent.platform.domain.exceptions import IdempotencyConflictException
from property_agent.platform.infrastructure.orm_models import IdempotencyRecordModel


def _hash_dict(data: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of a dictionary."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


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
            # New request — record the hash, proceed
            self._session.add(IdempotencyRecordModel(
                actor_id=actor_id,
                operation=operation,
                key=key,
                request_hash=request_hash,
            ))
            return None

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
        record = (
            self._session.query(IdempotencyRecordModel)
            .filter_by(actor_id=actor_id, operation=operation, key=key)
            .first()
        )
        if record:
            record.resource_id = resource_id
            record.response_snapshot = response_snapshot