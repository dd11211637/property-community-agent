"""Platform service errors — backward-compatible re-exports.

Canonical exception classes now live in:
- domain/exceptions.py

This module re-exports for backward compatibility.
"""
from __future__ import annotations

from property_agent.platform.domain.exceptions import (  # noqa: F401
    AuthError,
    ConfirmationError,
    IdempotencyConflictException as IdempotencyConflict,
    IdempotencyKeyRequiredException,
    InvalidConfirmationTokenException,
    PlatformError,
)