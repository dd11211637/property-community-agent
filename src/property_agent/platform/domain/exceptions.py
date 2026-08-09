"""Platform domain exceptions — PF-04, PF-05, PF-06 error types.

All exceptions carry a `code` string for machine-readable error handling
and a `status_code` for HTTP response mapping.
"""
from __future__ import annotations


class PlatformError(Exception):
    """Base error for platform services."""
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class IdempotencyKeyRequiredException(PlatformError):
    """Raised when Idempotency-Key header is missing on a write endpoint."""
    def __init__(self) -> None:
        super().__init__(
            code="IDEMPOTENCY_KEY_REQUIRED",
            message="Idempotency-Key header is required for write operations.",
            status_code=400,
        )


class IdempotencyConflictException(PlatformError):
    """Same idempotency key but different request parameters."""
    def __init__(self, actor_id: str, operation: str, key: str) -> None:
        super().__init__(
            code="IDEMPOTENCY_CONFLICT",
            message=f"Idempotency key conflict: actor={actor_id}, op={operation}, key={key}",
            status_code=409,
        )


class ConfirmationError(PlatformError):
    """Confirmation token validation failure (base class for backward compatibility)."""
    pass


class InvalidConfirmationTokenException(ConfirmationError):
    """Confirmation token validation failure — invalid, expired, consumed, or mismatch."""
    def __init__(self, reason: str = "Invalid confirmation token") -> None:
        super().__init__(
            code="INVALID_CONFIRMATION_TOKEN",
            message=reason,
            status_code=400,
        )


class AuthError(PlatformError):
    """Authentication or authorization failure."""
    pass