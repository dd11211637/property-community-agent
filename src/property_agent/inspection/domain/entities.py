from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None

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
