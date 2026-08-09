from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from property_agent.repair.domain.enums import (
    ActionCode,
    RepairCategory,
    Urgency,
    WorkOrderStatus,
)
from property_agent.repair.domain.errors import invalid_transition, validation_error

TRANSITIONS: dict[tuple[WorkOrderStatus, ActionCode], WorkOrderStatus] = {
    (WorkOrderStatus.PENDING_ASSIGNMENT, ActionCode.ASSIGN): WorkOrderStatus.PENDING_ACCEPTANCE,
    (WorkOrderStatus.PENDING_ACCEPTANCE, ActionCode.ACCEPT): WorkOrderStatus.PROCESSING,
    (WorkOrderStatus.PENDING_ACCEPTANCE, ActionCode.REJECT): WorkOrderStatus.PENDING_ASSIGNMENT,
    (
        WorkOrderStatus.PROCESSING,
        ActionCode.SUBMIT_COMPLETION,
    ): WorkOrderStatus.PENDING_VERIFICATION,
    (WorkOrderStatus.PENDING_VERIFICATION, ActionCode.VERIFY_PASS): WorkOrderStatus.CLOSED,
    (WorkOrderStatus.PENDING_VERIFICATION, ActionCode.REQUEST_REWORK): WorkOrderStatus.REWORKING,
    (
        WorkOrderStatus.REWORKING,
        ActionCode.SUBMIT_REWORK_COMPLETION,
    ): WorkOrderStatus.PENDING_VERIFICATION,
}


@dataclass(slots=True)
class WorkOrder:
    id: UUID
    community_id: UUID
    business_no: str
    house_id: UUID
    reporter_id: UUID
    category: RepairCategory
    location: str
    description: str
    urgency: Urgency
    create_idempotency_key: str
    status: WorkOrderStatus = WorkOrderStatus.PENDING_ASSIGNMENT
    assignee_id: UUID | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None
    has_review: bool = False

    def transition(self, action: ActionCode, *, now: datetime | None = None) -> WorkOrderStatus:
        target = TRANSITIONS.get((self.status, action))
        if target is None:
            raise invalid_transition(
                self.status.value,
                action.value,
                [item.value for item in self.state_actions()],
            )
        if action == ActionCode.ASSIGN and self.assignee_id is None:
            raise validation_error("An assignee is required before assignment.")
        if action == ActionCode.REJECT:
            self.assignee_id = None
        self.status = target
        self.version += 1
        self.updated_at = now or datetime.now(UTC)
        if target == WorkOrderStatus.CLOSED:
            self.closed_at = self.updated_at
        return target

    def touch(self, *, now: datetime | None = None) -> None:
        self.version += 1
        self.updated_at = now or datetime.now(UTC)

    def state_actions(self) -> tuple[ActionCode, ...]:
        actions: list[ActionCode] = [
            action for (status, action), _target in TRANSITIONS.items() if status == self.status
        ]
        if self.status in {WorkOrderStatus.PROCESSING, WorkOrderStatus.REWORKING}:
            actions.append(ActionCode.RECORD_PROGRESS)
        if self.status == WorkOrderStatus.CLOSED and not self.has_review:
            actions.append(ActionCode.CREATE_REVIEW)
        return tuple(actions)
