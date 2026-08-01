from datetime import datetime
from uuid import uuid4

from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.roles import Role


def require_role(context: RequestContext, *roles: Role) -> None:
    if not context.has_any_role(*roles):
        raise BusinessError("FORBIDDEN", "You are not allowed to perform this operation.", 403)


def require_idempotency_key(key: str) -> None:
    if not key or not key.strip() or len(key) > 128:
        raise BusinessError(
            "VALIDATION_ERROR",
            "Idempotency-Key is required and must not exceed 128 characters.",
            422,
        )


def validate_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 100 or offset < 0:
        raise BusinessError(
            "VALIDATION_ERROR",
            "Pagination must use offset >= 0 and limit between 1 and 100.",
            422,
        )


def required_text(value: str | None, message: str) -> str:
    if value is None or not value.strip():
        raise BusinessError("VALIDATION_ERROR", message, 422)
    return value.strip()


def new_business_no(now: datetime, prefix: str) -> str:
    return f"{prefix}-{now:%Y%m%d}-{uuid4().hex[:8].upper()}"
