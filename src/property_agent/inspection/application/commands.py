from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventType,
    TaskAction,
    TaskRecordType,
)


@dataclass(frozen=True, slots=True)
class CreateInspectionTaskCommand:
    title: str
    description: str
    route_points: tuple[str, ...]
    planned_at: datetime | None = None
    due_at: datetime | None = None
    attachment_ids: tuple[UUID, ...] = ()
    confirmation_token: str | None = None
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ExecuteTaskActionCommand:
    action: TaskAction
    expected_version: int
    assignee_id: UUID | None = None
    note: str | None = None
    record_type: TaskRecordType | None = None
    point: str | None = None
    attachment_ids: tuple[UUID, ...] = ()
    is_supplement: bool = False
    actual_time: datetime | None = None
    supplement_reason: str | None = None
    confirmation_token: str | None = None
    # P0 审批原子化：见 CreateWorkOrderCommand.approval_ref 注释。
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AddAiSuggestionCommand:
    point: str
    finding: str
    severity: str = "MEDIUM"
    model: str = "inspection-ai"
    confirmation_token: str | None = None
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ConfirmAiSuggestionsCommand:
    pass


@dataclass(frozen=True, slots=True)
class CreateSecurityEventCommand:
    source_task_id: UUID | None
    event_type: EventType
    risk_level: EventRiskLevel
    location: str
    description: str
    confirmation_token: str
    report_source: str = "MANUAL"
    attachment_ids: tuple[UUID, ...] = ()
    # P0 审批原子化：见 CreateWorkOrderCommand.approval_ref 注释。
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ExecuteEventActionCommand:
    action: EventAction
    expected_version: int
    assignee_id: UUID | None = None
    note: str | None = None
    attachment_ids: tuple[UUID, ...] = ()
    confirmation_token: str | None = None
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class InspectionTaskSearch:
    statuses: tuple[str, ...] = ()
    assigned_to_me: bool = False
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SecurityEventSearch:
    statuses: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
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
