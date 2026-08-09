from typing import Any

from property_agent.platform.errors import BusinessError


def validation_error(message: str, **details: Any) -> BusinessError:
    return BusinessError("VALIDATION_ERROR", message, 422, details or None)


def forbidden() -> BusinessError:
    return BusinessError("FORBIDDEN", "You are not allowed to perform this operation.", 403)


def not_found() -> BusinessError:
    return BusinessError("RESOURCE_NOT_FOUND", "Announcement was not found.", 404)


def invalid_transition(
    current_status: str, action: str, available_actions: list[str]
) -> BusinessError:
    return BusinessError(
        "INVALID_TRANSITION",
        f"Action {action} is not allowed from {current_status}.",
        409,
        {"current_status": current_status, "available_actions": available_actions},
    )


def version_conflict(current_version: int) -> BusinessError:
    return BusinessError(
        "VERSION_CONFLICT",
        "The announcement was modified by another request.",
        409,
        {"current_version": current_version},
    )


def idempotency_conflict() -> BusinessError:
    return BusinessError(
        "IDEMPOTENCY_CONFLICT",
        "The idempotency key was already used with different parameters.",
        409,
    )


def empty_audience() -> BusinessError:
    return BusinessError("EMPTY_AUDIENCE", "The announcement audience must not be empty.", 422)


def confirmation_required() -> BusinessError:
    return BusinessError(
        "CONFIRMATION_REQUIRED", "A confirmation token is required for publishing.", 422
    )
