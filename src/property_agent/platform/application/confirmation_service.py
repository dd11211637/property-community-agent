"""
Application-layer confirmation token service — PF-04.

Provides ConfirmationService for generating and validating secondary-confirmation
tokens bound to actor, action, parameter hash, and expiration time.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.domain.exceptions import InvalidConfirmationTokenException
from property_agent.platform.infrastructure.orm_models import ConfirmationTokenModel

CONFIRMATION_TTL_MINUTES = 5


def _hash_dict(data: dict[str, Any]) -> str:
    """Deterministic SHA-256 of a parameter dictionary.

    Delegates to the single canonical algorithm so that tokens generated via
    ``POST /api/confirmations`` (raw JSON parameters) match the hash computed
    by a business service from its parsed command object.
    """
    return canonical_hash(data)


class ConfirmationService:
    """Generates and validates confirmation tokens for write operations.

    Confirmation tokens provide a secondary approval step for sensitive write
    operations. Each token is bound to:
    - actor_id: who requested the operation
    - action: what operation type
    - parameter_hash: SHA-256 of the sorted parameters (prevents tampering)
    - expires_at: TTL-based expiration (default 5 minutes)

    Usage::

        svc = ConfirmationService(db_session)
        token = svc.generate_token(actor_id=..., action="DELETE_BILL", params={...})
        # ... send token to user for confirmation ...
        svc.validate_and_consume_token(
            token=token, actor_id=..., action="DELETE_BILL", params={...}
        )
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def generate_token(
        self,
        *,
        actor_id: UUID,
        action: str,
        params: dict[str, Any],
    ) -> str:
        """Generate a confirmation token bound to actor, action, and parameter hash.

        Returns the token string to be sent to the user for confirmation.
        """
        token = secrets.token_urlsafe(32)
        parameter_hash = _hash_dict(params)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=CONFIRMATION_TTL_MINUTES)

        self._session.add(
            ConfirmationTokenModel(
                token=token,
                actor_id=actor_id,
                action=action,
                parameter_hash=parameter_hash,
                expires_at=expires_at,
            )
        )
        # Some production session factories deliberately disable autoflush.
        # The Agent signs and validates generic confirmation tokens in the
        # same transaction, so make the newly issued token query-visible now.
        self._session.flush()
        return token

    def validate_and_consume_token(
        self,
        *,
        token: str,
        actor_id: UUID,
        action: str,
        params: dict[str, Any],
    ) -> None:
        """Validate and consume a confirmation token.

        Computes parameter_hash from params internally, then verifies:
        - Token exists
        - Not already consumed
        - Not expired
        - actor_id matches
        - action matches
        - parameter_hash matches (params unchanged since generation)

        Raises:
            InvalidConfirmationTokenException: if any validation fails.
        """
        parameter_hash = _hash_dict(params)

        record = (
            self._session.query(ConfirmationTokenModel)
            .filter_by(token=token)
            .with_for_update()
            .first()
        )

        if record is None:
            raise InvalidConfirmationTokenException("Confirmation token not found.")

        if record.consumed_at is not None:
            raise InvalidConfirmationTokenException("Token has already been used.")

        expires = record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise InvalidConfirmationTokenException("Confirmation token has expired.")

        if record.actor_id != actor_id:
            raise InvalidConfirmationTokenException("Token actor mismatch.")

        if record.action != action:
            raise InvalidConfirmationTokenException("Token action mismatch.")

        if record.parameter_hash != parameter_hash:
            raise InvalidConfirmationTokenException(
                "Parameters have changed since token was generated; please re-confirm."
            )

        record.consumed_at = datetime.now(timezone.utc)

    # -- backward-compatible aliases --

    def generate(
        self,
        *,
        actor_id: UUID,
        action: str,
        parameters: dict[str, Any],
    ) -> str:
        """Backward-compatible alias for generate_token."""
        return self.generate_token(actor_id=actor_id, action=action, params=parameters)

    def consume(
        self,
        *,
        token: str,
        actor_id: UUID,
        action: str,
        parameter_hash: str,
        request_id: str,
    ) -> None:
        """Backward-compatible consume (accepts pre-computed hash)."""
        record = (
            self._session.query(ConfirmationTokenModel)
            .filter_by(token=token)
            .with_for_update()
            .first()
        )

        if record is None:
            raise InvalidConfirmationTokenException("Confirmation token not found.")

        if record.consumed_at is not None:
            raise InvalidConfirmationTokenException("Token has already been used.")

        expires = record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise InvalidConfirmationTokenException("Confirmation token has expired.")

        if record.actor_id != actor_id:
            raise InvalidConfirmationTokenException("Token actor mismatch.")

        if record.action != action:
            raise InvalidConfirmationTokenException("Token action mismatch.")

        if record.parameter_hash != parameter_hash:
            raise InvalidConfirmationTokenException("Parameters have changed; please re-confirm.")

        record.consumed_at = datetime.now(timezone.utc)
