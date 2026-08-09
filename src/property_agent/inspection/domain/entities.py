from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventStatus,
    EventType,
    TaskAction,
    TaskStatus,
)
from property_agent.inspection.domain.errors import (
    handover_required,
    invalid_transition,
    validation_error,
)

TASK_TRANSITIONS: dict[tuple[TaskStatus, TaskAction], TaskStatus] = {
    (TaskStatus.PLANNED, TaskAction.ASSIGN): TaskStatus.ASSIGNED,
    (TaskStatus.ASSIGNED, TaskAction.START): TaskStatus.IN_PROGRESS,
    (TaskStatus.IN_PROGRESS, TaskAction.SUBMIT_RECORDS): TaskStatus.SUBMITTED,
    (TaskStatus.SUBMITTED, TaskAction.COMPLETE): TaskStatus.COMPLETED,
}


@dataclass(slots=True)
class AiSuggestion:
    """AI 异常建议的数据结构（PRD 6.4）。

    ``pending_confirm`` 为 True 时表示"待人工确认"；经授权管理者确认后
    置为 False，并写入 ``confirmed_by`` / ``confirmed_at``。
    """

    id: UUID
    point: str
    finding: str
    severity: str  # LOW / MEDIUM / HIGH
    model: str
    generated_at: datetime
    pending_confirm: bool = True
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "point": self.point,
            "finding": self.finding,
            "severity": self.severity,
            "model": self.model,
            "generated_at": self.generated_at.isoformat(),
            "pending_confirm": self.pending_confirm,
            "confirmed_by": str(self.confirmed_by) if self.confirmed_by else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AiSuggestion":
        return cls(
            id=UUID(data["id"]),
            point=data["point"],
            finding=data["finding"],
            severity=data["severity"],
            model=data["model"],
            generated_at=datetime.fromisoformat(data["generated_at"]),
            pending_confirm=data.get("pending_confirm", True),
            confirmed_by=UUID(data["confirmed_by"]) if data.get("confirmed_by") else None,
            confirmed_at=datetime.fromisoformat(data["confirmed_at"])
            if data.get("confirmed_at")
            else None,
        )

EVENT_TRANSITIONS: dict[tuple[EventStatus, EventAction], EventStatus] = {
    (EventStatus.REPORTED, EventAction.ASSIGN): EventStatus.ASSIGNED,
    (EventStatus.ASSIGNED, EventAction.SUBMIT_DISPOSAL): EventStatus.PENDING_REVIEW,
    (EventStatus.PENDING_REVIEW, EventAction.REVIEW_PASS): EventStatus.CLOSED,
    (EventStatus.PENDING_REVIEW, EventAction.RETURN): EventStatus.ASSIGNED,
}


@dataclass(slots=True)
class InspectionTask:
    id: UUID
    community_id: UUID
    business_no: str
    title: str
    description: str
    route_points: tuple[str, ...]
    created_by: UUID
    create_idempotency_key: str
    status: TaskStatus = TaskStatus.PLANNED
    assignee_id: UUID | None = None
    planned_at: datetime | None = None
    due_at: datetime | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None
    ai_suggestions: tuple[AiSuggestion, ...] = ()
    ai_pending_confirm: bool = False

    def transition(self, action: TaskAction, *, now: datetime | None = None) -> TaskStatus:
        target = TASK_TRANSITIONS.get((self.status, action))
        if target is None:
            raise invalid_transition(
                self.status.value,
                action.value,
                [item.value for item in self.state_actions()],
            )
        if action == TaskAction.ASSIGN and self.assignee_id is None:
            raise validation_error("An assignee is required before assignment.")
        self.status = target
        self.version += 1
        self.updated_at = now or datetime.now(UTC)
        if target == TaskStatus.COMPLETED:
            self.closed_at = self.updated_at
        return target

    def touch(self, *, now: datetime | None = None) -> None:
        self.version += 1
        self.updated_at = now or datetime.now(UTC)

    # ----------------------------- AI 异常建议（PRD 6.4） -----------------------------
    def add_ai_suggestion(self, suggestion: AiSuggestion) -> None:
        """追加一条 AI 异常建议并置为待人工确认。"""
        self.ai_suggestions = (*self.ai_suggestions, suggestion)
        self.ai_pending_confirm = True
        self.version += 1
        self.updated_at = datetime.now(UTC)

    def confirm_ai_suggestions(self, *, confirmed_by: UUID, now: datetime | None = None) -> None:
        """授权管理者确认全部待确认建议，清除待确认标识。"""
        now = now or datetime.now(UTC)
        self.ai_suggestions = tuple(
            replace(
                s,
                pending_confirm=False,
                confirmed_by=confirmed_by,
                confirmed_at=now,
            )
            for s in self.ai_suggestions
        )
        self.ai_pending_confirm = False
        self.version += 1
        self.updated_at = now

    def state_actions(self) -> tuple[TaskAction, ...]:
        actions: list[TaskAction] = [
            action
            for (status, action), _target in TASK_TRANSITIONS.items()
            if status == self.status
        ]
        if self.status in {TaskStatus.IN_PROGRESS, TaskStatus.SUBMITTED}:
            actions.append(TaskAction.ADD_RECORD)
        return tuple(actions)


@dataclass(slots=True)
class SecurityEvent:
    id: UUID
    community_id: UUID
    business_no: str
    reporter_id: UUID
    event_type: EventType
    risk_level: EventRiskLevel
    location: str
    description: str
    create_idempotency_key: str
    status: EventStatus = EventStatus.REPORTED
    source_task_id: UUID | None = None
    assignee_id: UUID | None = None
    grade_confirmed_by: UUID | None = None
    report_source: str = "MANUAL"  # PRD 6.4：MANUAL（人工直接上报）/ AI（模型辅助）
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None

    def touch(self, *, now: datetime | None = None) -> None:
        self.version += 1
        self.updated_at = now or datetime.now(UTC)

    def transition(self, action: EventAction, *, now: datetime | None = None) -> EventStatus:
        target = EVENT_TRANSITIONS.get((self.status, action))
        if target is None:
            raise invalid_transition(
                self.status.value,
                action.value,
                [item.value for item in self.state_actions()],
            )
        if action == EventAction.ASSIGN and self.assignee_id is None:
            raise validation_error("A handler is required before assignment.")
        if (
            action == EventAction.REVIEW_PASS
            and self.risk_level == EventRiskLevel.HIGH_RISK
            and self.grade_confirmed_by is None
        ):
            raise handover_required()
        self.status = target
        self.version += 1
        self.updated_at = now or datetime.now(UTC)
        if target == EventStatus.CLOSED:
            self.closed_at = self.updated_at
        return target

    def state_actions(self) -> tuple[EventAction, ...]:
        return tuple(
            action
            for (status, action), _target in EVENT_TRANSITIONS.items()
            if status == self.status
        )
