from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from property_agent.repair.domain.enums import (
    ActionCode,
    ProcessRecordType,
    RepairCategory,
    Urgency,
)


@dataclass(frozen=True, slots=True)
class CreateWorkOrderCommand:
    house_id: UUID
    category: RepairCategory
    location: str
    description: str
    urgency: Urgency
    confirmation_token: str
    attachment_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecuteActionCommand:
    action: ActionCode
    expected_version: int
    assignee_id: UUID | None = None
    reason: str | None = None
    note: str | None = None
    record_type: ProcessRecordType | None = None
    appointment_at: datetime | None = None
    attachment_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class CreateReviewCommand:
    rating: int
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class WorkOrderSearch:
    house_id: UUID | None = None
    statuses: tuple[str, ...] = ()
    assigned_to_me: bool = False
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    entry_type: str
    action: str
    operator_id: UUID
    created_at: datetime
    from_status: str | None = None
    to_status: str | None = None
    reason: str | None = None
    note: str | None = None
    attachment_ids: tuple[UUID, ...] = field(default_factory=tuple)
