from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class BusinessError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def validation_error(message: str, **details: Any) -> BusinessError:
    return BusinessError("VALIDATION_ERROR", message, 422, details or None)


def forbidden(message: str = "You are not allowed to perform this operation.") -> BusinessError:
    return BusinessError("FORBIDDEN", message, 403)


def not_found() -> BusinessError:
    return BusinessError("RESOURCE_NOT_FOUND", "Work order was not found.", 404)


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
        "The work order was modified by another request.",
        409,
        {"current_version": current_version},
    )


def idempotency_conflict() -> BusinessError:
    return BusinessError(
        "IDEMPOTENCY_CONFLICT",
        "The idempotency key was already used with different parameters.",
        409,
    )


def handover_required(
    handover_ticket_id: Any = None, notified_staff: int | None = None
) -> BusinessError:
    """High-risk case routed to a human.

    When a handover ticket was actually created, its ID is returned in
    ``details`` so the caller can track the manual follow-up instead of
    receiving a bare rejection.
    """
    details: dict[str, Any] = {}
    if handover_ticket_id is not None:
        details["handover_ticket_id"] = str(handover_ticket_id)
    if notified_staff is not None:
        details["notified_staff"] = notified_staff
    return BusinessError(
        "HANDOVER_REQUIRED",
        "High-risk reports must be handed over to authorized personnel.",
        422,
        details or None,
    )
