"""
Application-layer audit service — PF-06.

Provides AuditService, @audit_log decorator, and DataMasker for sensitive
field masking (phone, password, keys, attachment URLs).
"""
from __future__ import annotations

import functools
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from property_agent.platform.infrastructure.orm_models import AuditLogModel

# ---------------------------------------------------------------------------
# Sensitive field names and regex patterns
# ---------------------------------------------------------------------------

# Fields that should be fully redacted (***REDACTED***)
FULL_REDACT_FIELDS = {"password", "token", "secret", "id_card", "bank_account"}

# Fields that should get phone regex masking (138****1234)
PHONE_FIELDS = {"phone", "mobile", "tel", "telephone", "contact"}

# All sensitive field names (for backward compatibility)
SENSITIVE_FIELDS = FULL_REDACT_FIELDS | PHONE_FIELDS

# Chinese mobile phone number: 1[3-9]XXXXXXXX
PHONE_RE = re.compile(r"(1[3-9]\d)\d{4}(\d{4})")
# Generic key/secret/token masking
KEY_LIKE_RE = re.compile(
    r"(Bearer\s+|secret[=:]\s*|api[_-]?key[=:]\s*|token[=:]\s*)([A-Za-z0-9+/=_-]{8,})",
    re.IGNORECASE,
)
# Sensitive URL query parameters
ATTACHMENT_URL_RE = re.compile(
    r"(?<=[?&])(token|signature|secret|auth)=([^&\s]+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# DataMasker — PF-06 desensitization engine
# ---------------------------------------------------------------------------

class DataMasker:
    """Mask sensitive data before writing to AuditLog.parameter_summary.

    Masks:
    - Phone numbers: 138****1234
    - Password / secret / token fields: ***REDACTED***
    - Sensitive attachment URLs: masked query parameters
    """

    @staticmethod
    def mask_sensitive_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
        """Mask sensitive fields in a parameter dictionary."""
        if data is None:
            return None
        return DataMasker._mask_dict(data)

    @staticmethod
    def mask_phone(text: str) -> str:
        """Mask a phone number string: 13812341234 -> 138****1234."""
        return PHONE_RE.sub(r"\1****\2", text)

    @staticmethod
    def mask_secrets(text: str) -> str:
        """Mask bearer tokens, secrets, and API keys in a string."""
        return KEY_LIKE_RE.sub(r"\1***REDACTED***", text)

    @staticmethod
    def mask_attachment_urls(text: str) -> str:
        """Mask sensitive query parameters in attachment URLs."""
        return ATTACHMENT_URL_RE.sub(r"\1=***REDACTED***", text)

    # -- internal helpers --

    @classmethod
    def _mask_dict(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively mask sensitive fields in a dictionary."""
        masked: dict[str, Any] = {}
        for key, value in data.items():
            key_lower = key.lower()
            if key_lower in FULL_REDACT_FIELDS or any(
                kw in key_lower for kw in FULL_REDACT_FIELDS
            ):
                masked[key] = "***REDACTED***"
            elif key_lower in PHONE_FIELDS and isinstance(value, str):
                masked[key] = cls.mask_phone(value)
            elif isinstance(value, str):
                masked[key] = cls._mask_string_value(key, value)
            elif isinstance(value, dict):
                masked[key] = cls._mask_dict(value)
            elif isinstance(value, list):
                masked[key] = [
                    cls._mask_dict(v) if isinstance(v, dict) else
                    cls._mask_string_value(key, v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                masked[key] = value
        return masked

    @classmethod
    def _mask_string_value(cls, key: str, value: str) -> str:
        """Apply progressive masking to a string value based on field name."""
        result = value
        # Phone-like fields
        if key.lower() in {"phone", "mobile", "tel", "telephone", "contact"}:
            result = cls.mask_phone(result)
        # URL-like fields
        if key.lower() in {"url", "attachment_url", "file_url", "avatar_url", "image_url"}:
            result = cls.mask_attachment_urls(result)
        # Token/secret in string values
        if any(kw in key.lower() for kw in {"token", "secret", "key", "auth", "password"}):
            result = cls.mask_secrets(result)
        return result


# ---------------------------------------------------------------------------
# AuditService — PF-06 audit logging
# ---------------------------------------------------------------------------

class AuditService:
    """Writes audit log entries with sensitive data masking.

    Usage::

        svc = AuditService(db_session)
        svc.log(actor_id=..., community_id=..., action="LOGIN_SUCCESS",
                resource_type="USER", resource_id=..., result="SUCCESS", request_id=...)
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def log(
        self,
        *,
        actor_id: UUID,
        community_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        parameter_summary: dict[str, Any] | None = None,
        result: str = "SUCCESS",
        request_id: str,
    ) -> None:
        """Write an audit log entry with sensitive data masking.

        The parameter_summary is automatically masked via DataMasker before
        being persisted to the AuditLog table.
        """
        masked = DataMasker.mask_sensitive_data(parameter_summary)

        self._session.add(AuditLogModel(
            actor_id=actor_id,
            community_id=community_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            parameter_summary=masked,
            result=result,
            request_id=request_id,
        ))


# ---------------------------------------------------------------------------
# @audit_log decorator — PF-06
# ---------------------------------------------------------------------------

def audit_log(action: str, resource_type: str) -> Callable:
    """Decorator that automatically writes an audit log entry.

    Applied to FastAPI endpoint functions or service methods. The decorated
    function must receive a SQLAlchemy Session as its first argument, or the
    session must be accessible via keyword argument ``db``.

    Audit is written AFTER the function completes successfully. If the function
    raises an exception, a FAILURE audit entry is written.

    Usage::

        @audit_log(action="BILL_QUERY", resource_type="BILL")
        async def query_bill(db: Session, bill_id: str, ctx: RequestContext):
            ...

    The decorator extracts:
    - actor_id / community_id from RequestContext.current()
    - request_id from RequestContext.current()
    - parameter_summary from function kwargs (excluding db, context, ctx)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            from property_agent.platform.adapters.api.dependencies import RequestContext
            ctx = RequestContext.current()
            db = _extract_session(kwargs)

            # Build parameter summary (exclude db, context objects)
            param_summary = _build_param_summary(kwargs)

            try:
                result = await func(*args, **kwargs)
                if db is not None and ctx is not None:
                    svc = AuditService(db)
                    svc.log(
                        actor_id=ctx.actor_id,
                        community_id=ctx.community_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=_extract_resource_id(kwargs, result),
                        parameter_summary=param_summary,
                        result="SUCCESS",
                        request_id=ctx.request_id,
                    )
                return result
            except Exception:
                if db is not None and ctx is not None:
                    svc = AuditService(db)
                    svc.log(
                        actor_id=ctx.actor_id,
                        community_id=ctx.community_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=_extract_resource_id(kwargs, None),
                        parameter_summary=param_summary,
                        result="FAILURE",
                        request_id=ctx.request_id,
                    )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            from property_agent.platform.adapters.api.dependencies import RequestContext
            ctx = RequestContext.current()
            db = _extract_session(kwargs)

            param_summary = _build_param_summary(kwargs)

            try:
                result = func(*args, **kwargs)
                if db is not None and ctx is not None:
                    svc = AuditService(db)
                    svc.log(
                        actor_id=ctx.actor_id,
                        community_id=ctx.community_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=_extract_resource_id(kwargs, result),
                        parameter_summary=param_summary,
                        result="SUCCESS",
                        request_id=ctx.request_id,
                    )
                return result
            except Exception:
                if db is not None and ctx is not None:
                    svc = AuditService(db)
                    svc.log(
                        actor_id=ctx.actor_id,
                        community_id=ctx.community_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=_extract_resource_id(kwargs, None),
                        parameter_summary=param_summary,
                        result="FAILURE",
                        request_id=ctx.request_id,
                    )
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_EXCLUDED_PARAM_KEYS = {"db", "session", "context", "ctx", "request", "self", "cls"}


def _extract_session(kwargs: dict[str, Any]) -> Session | None:
    """Extract a SQLAlchemy Session from function kwargs."""
    for key in ("db", "session"):
        val = kwargs.get(key)
        if isinstance(val, Session):
            return val
    return None


def _build_param_summary(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Build a parameter summary dict from function kwargs, excluding internal objects."""
    summary: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _EXCLUDED_PARAM_KEYS:
            continue
        # Skip SQLAlchemy and Pydantic model objects (non-serializable)
        if hasattr(value, "__class__") and hasattr(value.__class__, "__tablename__"):
            continue
        try:
            # Try to serialize; skip non-serializable objects
            if isinstance(value, (str, int, float, bool, type(None))):
                summary[key] = value
            elif isinstance(value, (list, tuple)):
                summary[key] = [str(v) for v in value]
            elif isinstance(value, dict):
                summary[key] = value
            elif isinstance(value, UUID):
                summary[key] = str(value)
            else:
                summary[key] = str(value)
        except Exception:
            summary[key] = "<non-serializable>"
    return summary if summary else None


def _extract_resource_id(kwargs: dict[str, Any], result: Any) -> str | None:
    """Try to extract a resource_id from function kwargs or result."""
    # Check common resource ID kwargs
    for key in ("resource_id", "bill_id", "order_id", "repair_id", "event_id",
                "announcement_id", "inspection_id", "handover_id", "message_id"):
        val = kwargs.get(key)
        if val is not None:
            return str(val)
    # Try to extract from result (e.g., Pydantic model with id field)
    if result is not None and hasattr(result, "id"):
        return str(result.id)
    if isinstance(result, dict) and "id" in result:
        return str(result["id"])
    return None