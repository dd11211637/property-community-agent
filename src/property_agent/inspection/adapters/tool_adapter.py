from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from property_agent.inspection.adapters.presentation import event_data, task_data
from property_agent.inspection.application.commands import (
    CreateInspectionTaskCommand,
    CreateSecurityEventCommand,
    ExecuteEventActionCommand,
    ExecuteTaskActionCommand,
    InspectionTaskSearch,
    SecurityEventSearch,
)
from property_agent.inspection.application.ports import RequestContext
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventType,
    TaskAction,
    TaskRecordType,
)


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchInspectionTasksInput(ToolInput):
    statuses: list[str] = Field(default_factory=list)
    assigned_to_me: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CreateInspectionTaskInput(ToolInput):
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    route_points: list[str] = Field(min_length=1)
    planned_at: datetime | None = None
    due_at: datetime | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ExecuteTaskActionInput(ToolInput):
    task_id: UUID
    action: Literal["ASSIGN", "START", "SUBMIT_RECORDS", "ADD_RECORD", "COMPLETE"]
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    assignee_id: UUID | None = None
    note: str | None = None
    record_type: TaskRecordType | None = None
    point: str | None = None
    confirmation_token: str | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)
    is_supplement: bool = False
    actual_time: datetime | None = None


class SearchSecurityEventsInput(ToolInput):
    statuses: list[str] = Field(default_factory=list)
    risk_levels: list[str] = Field(default_factory=list)
    assigned_to_me: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CreateSecurityEventInput(ToolInput):
    source_task_id: UUID | None = None
    event_type: EventType
    risk_level: EventRiskLevel
    location: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    confirmation_token: str = Field(min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ExecuteEventActionInput(ToolInput):
    event_id: UUID
    action: Literal["ASSIGN", "SUBMIT_DISPOSAL", "REVIEW_PASS", "RETURN"]
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    assignee_id: UUID | None = None
    note: str | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_inspection_tasks": {
        "name": "search_inspection_tasks",
        "description": "Search authorized inspection tasks using structured filters.",
        "parameters": SearchInspectionTasksInput.model_json_schema(),
    },
    "create_inspection_task": {
        "name": "create_inspection_task",
        "description": (
            "Create an inspection plan after all fields and the user's confirmation "
            "have been collected."
        ),
        "parameters": CreateInspectionTaskInput.model_json_schema(),
    },
    "execute_inspection_task_action": {
        "name": "execute_inspection_task_action",
        "description": "Execute an authorized action against the current inspection-task state.",
        "parameters": ExecuteTaskActionInput.model_json_schema(),
    },
    "search_security_events": {
        "name": "search_security_events",
        "description": "Search authorized security events using structured filters.",
        "parameters": SearchSecurityEventsInput.model_json_schema(),
    },
    "create_security_event": {
        "name": "create_security_event",
        "description": (
            "Create a security event after the user's confirmation has been collected. "
            "High-risk events require human confirmation of grade and handling."
        ),
        "parameters": CreateSecurityEventInput.model_json_schema(),
    },
    "execute_security_event_action": {
        "name": "execute_security_event_action",
        "description": "Execute an authorized action against the current security-event state.",
        "parameters": ExecuteEventActionInput.model_json_schema(),
    },
}


class InspectionToolAdapter:
    """Framework-neutral tools; this class performs no intent detection or routing."""

    def __init__(
        self, task_service: InspectionTaskService, event_service: SecurityEventService
    ) -> None:
        self._task_service = task_service
        self._event_service = event_service

    def search_inspection_tasks(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = SearchInspectionTasksInput.model_validate(arguments)
        results = self._task_service.search_tasks(
            InspectionTaskSearch(
                statuses=tuple(payload.statuses),
                assigned_to_me=payload.assigned_to_me,
                limit=payload.limit,
                offset=payload.offset,
            ),
            context,
        )
        return {
            "items": [task_data(t, self._task_service, context) for t in results],
            "limit": payload.limit,
            "offset": payload.offset,
        }

    def create_inspection_task(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = CreateInspectionTaskInput.model_validate(arguments)
        result = self._task_service.create_task(
            CreateInspectionTaskCommand(
                title=payload.title,
                description=payload.description,
                route_points=tuple(payload.route_points),
                planned_at=payload.planned_at,
                due_at=payload.due_at,
                attachment_ids=tuple(payload.attachment_ids),
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return task_data(result, self._task_service, context)

    def execute_inspection_task_action(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = ExecuteTaskActionInput.model_validate(arguments)
        result = self._task_service.execute_task_action(
            payload.task_id,
            ExecuteTaskActionCommand(
                action=TaskAction(payload.action),
                expected_version=payload.expected_version,
                assignee_id=payload.assignee_id,
                note=payload.note,
                record_type=payload.record_type,
                point=payload.point,
                confirmation_token=payload.confirmation_token,
                attachment_ids=tuple(payload.attachment_ids),
                is_supplement=payload.is_supplement,
                actual_time=payload.actual_time,
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return task_data(result, self._task_service, context)

    def search_security_events(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = SearchSecurityEventsInput.model_validate(arguments)
        results = self._event_service.search_events(
            SecurityEventSearch(
                statuses=tuple(payload.statuses),
                risk_levels=tuple(payload.risk_levels),
                assigned_to_me=payload.assigned_to_me,
                limit=payload.limit,
                offset=payload.offset,
            ),
            context,
        )
        return {
            "items": [event_data(e, self._event_service, context) for e in results],
            "limit": payload.limit,
            "offset": payload.offset,
        }

    def create_security_event(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = CreateSecurityEventInput.model_validate(arguments)
        result = self._event_service.create_event(
            CreateSecurityEventCommand(
                source_task_id=payload.source_task_id,
                event_type=payload.event_type,
                risk_level=payload.risk_level,
                location=payload.location,
                description=payload.description,
                confirmation_token=payload.confirmation_token,
                attachment_ids=tuple(payload.attachment_ids),
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return event_data(result, self._event_service, context)

    def execute_security_event_action(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = ExecuteEventActionInput.model_validate(arguments)
        result = self._event_service.execute_event_action(
            payload.event_id,
            ExecuteEventActionCommand(
                action=EventAction(payload.action),
                expected_version=payload.expected_version,
                assignee_id=payload.assignee_id,
                note=payload.note,
                attachment_ids=tuple(payload.attachment_ids),
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return event_data(result, self._event_service, context)
