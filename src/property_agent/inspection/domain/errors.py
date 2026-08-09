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
    return BusinessError("RESOURCE_NOT_FOUND", "Inspection resource was not found.", 404)


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
        "The resource was modified by another request.",
        409,
        {"current_version": current_version},
    )


def idempotency_conflict() -> BusinessError:
    return BusinessError(
        "IDEMPOTENCY_CONFLICT",
        "The idempotency key was already used with different parameters.",
        409,
    )


def confirmation_required() -> BusinessError:
    return BusinessError(
        "CONFIRMATION_REQUIRED",
        "A confirmation token is required for this operation.",
        422,
    )


def handover_required() -> BusinessError:
    return BusinessError(
        "HANDOVER_REQUIRED",
        "High-risk events must be confirmed by authorized personnel.",
        422,
    )


def plan_conflict(overlaps_with: str | None = None) -> BusinessError:
    """计划时间与路线冲突（PRD 6.4：计划时间与路线冲突校验）。"""

    message = "The plan conflicts with an existing active task on the same route and time window."
    if overlaps_with:
        message = f"{message} Conflicting task: {overlaps_with}."
    return BusinessError("PLAN_CONFLICT", message, 409, {"conflicting_task": overlaps_with})


def supplement_reason_required() -> BusinessError:
    """补交记录必须说明实际原因（PRD 6.4：补交原因）。"""
    return BusinessError(
        "SUPPLEMENT_REASON_REQUIRED",
        "A supplement record must include the reason for the late submission.",
        422,
    )


def escalation_raised(resource_id: str, ticket_id: str) -> BusinessError:
    """高风险通知无可用值班人员，已升级到备用联系人（PRD 6.4：备用联系人/升级）。"""
    return BusinessError(
        "ESCALATION_RAISED",
        "No on-duty staff available; the high-risk event was escalated to a backup contact.",
        202,
        {"resource_id": resource_id, "ticket_id": ticket_id},
    )
