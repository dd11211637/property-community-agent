"""Typed inspection capabilities using the existing Application Services."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from property_agent.agent.capabilities.contracts import (
    CapabilityInput,
    CapabilityOutput,
    CapabilityRuntimeContext,
)
from property_agent.inspection.adapters.api.dependencies import ROLE_MAP
from property_agent.inspection.application.commands import (
    AddAiSuggestionCommand,
    CreateInspectionTaskCommand,
    CreateSecurityEventCommand,
    ExecuteEventActionCommand,
    ExecuteTaskActionCommand,
    InspectionTaskSearch,
    SecurityEventSearch,
)
from property_agent.inspection.domain.classification import normalize_security_event
from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    TaskAction,
    TaskRecordType,
)
from property_agent.inspection.domain.errors import BusinessError as InspectionBusinessError


class InspectionListInput(CapabilityInput):
    target: Literal["task", "event"] = "task"
    statuses: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
    assigned_to_me: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class InspectionTaskGetInput(CapabilityInput):
    task_id: UUID


class InspectionEventGetInput(CapabilityInput):
    event_id: UUID


class InspectionCreateInput(CapabilityInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    point: str = Field(min_length=1, max_length=255)
    route_points: tuple[str, ...] = ()
    planned_at: datetime | None = None
    due_at: datetime | None = None


class InspectionTaskActionInput(CapabilityInput):
    task_id: UUID
    expected_version: int = Field(ge=1)


class InspectionRecordInput(InspectionTaskActionInput):
    point: str = Field(min_length=1, max_length=255)
    note: str = Field(min_length=1, max_length=4000)
    record_type: str = "POINT_RECORD"
    is_supplement: bool = False
    actual_time: datetime | None = None
    supplement_reason: str | None = None


class InspectionAiSuggestInput(CapabilityInput):
    task_id: UUID
    point: str = Field(min_length=1, max_length=255)
    finding: str = Field(min_length=1, max_length=4000)
    severity: str = "MEDIUM"
    model: str = "inspection-ai"


class SecurityEventCreateInput(CapabilityInput):
    source_task_id: UUID | None = None
    event_type: str = "OTHER"
    risk_level: str = "MEDIUM"
    location: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4000)


class SecurityDisposalInput(CapabilityInput):
    event_id: UUID
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=4000)


class HighRiskCloseInput(CapabilityInput):
    event_id: UUID


class InspectionDataOutput(CapabilityOutput):
    data: dict[str, Any]


def _context(runtime: CapabilityRuntimeContext) -> Any:
    projector = getattr(runtime, "inspection_context_projector", None)
    if callable(projector):
        canonical = runtime.request_context
        projected = projector(canonical)
        expected_roles = frozenset(ROLE_MAP[role] for role in canonical.roles if role in ROLE_MAP)
        protected = (
            projected.actor_id == canonical.actor_id,
            projected.community_id == canonical.community_id,
            projected.execution_source == canonical.execution_source,
            projected.agent_lease == canonical.agent_lease,
            projected.roles == expected_roles,
        )
        if not all(protected):
            raise RuntimeError("inspection context projection changed trusted authority")
        return projected
    return runtime.request_context


def _brief(entity: Any) -> dict[str, Any]:
    values = {
        "id": str(entity.id),
        "business_no": getattr(entity, "business_no", None),
        "status": str(entity.status),
        "version": entity.version,
        "ai_pending_confirm": getattr(entity, "ai_pending_confirm", False),
        "report_source": str(getattr(entity, "report_source", "")),
    }
    for name in (
        "title",
        "description",
        "route_points",
        "event_type",
        "risk_level",
        "location",
        "assignee_id",
    ):
        value = getattr(entity, name, None)
        if value is not None:
            values[name] = str(value) if name == "assignee_id" else value
    return values


class InspectionAdapter:
    def __init__(self, task_service: Any, event_service: Any, operation: str) -> None:
        self._tasks = task_service
        self._events = event_service
        self._operation = operation

    def __call__(self, request: CapabilityInput, runtime: CapabilityRuntimeContext):
        context = _context(runtime)
        handler = getattr(self, f"_{self._operation}")
        try:
            return InspectionDataOutput(data=handler(request, runtime, context))
        except InspectionBusinessError as exc:
            from property_agent.agent.capabilities.contracts import CapabilityDomainError

            raise CapabilityDomainError(
                exc.code,
                exc.message,
                details=dict(exc.details or {}),
            ) from exc

    def _inspection_list(self, request: InspectionListInput, runtime, context):
        if request.target == "event":
            items = self._events.search_events(
                SecurityEventSearch(
                    statuses=request.statuses,
                    risk_levels=request.risk_levels,
                    assigned_to_me=request.assigned_to_me,
                    limit=request.limit,
                ),
                context,
            )
            return {"target": "event", "count": len(items), "items": [_brief(x) for x in items]}
        search = InspectionTaskSearch(
            statuses=request.statuses,
            assigned_to_me=request.assigned_to_me,
            limit=request.limit,
        )
        items = self._tasks.search_tasks(search, context)
        return {
            "target": "task",
            "count": len(items),
            "items": [_brief(x) for x in items],
            **self._tasks.summarize_tasks(search, context),
        }

    def _inspection_get_task(self, request: InspectionTaskGetInput, runtime, context):
        task = self._tasks.get_task(request.task_id, context)
        return {
            "task": {
                **_brief(task),
                "available_actions": [
                    x.value for x in self._tasks.available_task_actions(task, context)
                ],
            },
            "timeline": [asdict(x) for x in self._tasks.task_timeline(task.id, context)],
        }

    def _inspection_get_event(self, request: InspectionEventGetInput, runtime, context):
        event = self._events.get_event(request.event_id, context)
        return {
            "event": {
                **_brief(event),
                "available_actions": [
                    x.value for x in self._events.available_event_actions(event, context)
                ],
            },
            "timeline": [asdict(x) for x in self._events.event_timeline(event.id, context)],
        }

    def _inspection_create(self, request: InspectionCreateInput, runtime, context):
        write = self._write(runtime)
        command = CreateInspectionTaskCommand(
            request.title,
            request.description,
            request.route_points or (request.point,),
            request.planned_at,
            request.due_at,
            confirmation_token=write.confirmation_token,
            approval_ref=write.approval_ref,
        )
        task = self._tasks.create_task(command, context, idempotency_key=write.idempotency_key)
        return {"task": _brief(task), "idempotency_key": write.idempotency_key}

    def _inspection_start_task(self, request: InspectionTaskActionInput, runtime, context):
        write = self._write(runtime)
        command = ExecuteTaskActionCommand(
            TaskAction.START,
            request.expected_version,
            confirmation_token=write.confirmation_token,
            approval_ref=write.approval_ref,
        )
        task = self._tasks.execute_task_action(
            request.task_id, command, context, idempotency_key=write.idempotency_key
        )
        return {"task": _brief(task), "idempotency_key": write.idempotency_key}

    def _record(self, request: InspectionRecordInput, runtime, context, *, final: bool):
        write = self._write(runtime)
        command = ExecuteTaskActionCommand(
            TaskAction.SUBMIT_RECORDS if final else TaskAction.ADD_RECORD,
            request.expected_version,
            note=request.note,
            record_type=TaskRecordType(request.record_type),
            point=request.point,
            is_supplement=request.is_supplement,
            actual_time=request.actual_time,
            supplement_reason=request.supplement_reason,
            confirmation_token=write.confirmation_token,
            approval_ref=write.approval_ref,
        )
        task = self._tasks.execute_task_action(
            request.task_id, command, context, idempotency_key=write.idempotency_key
        )
        return {"task": _brief(task), "idempotency_key": write.idempotency_key}

    def _inspection_add_record(self, request, runtime, context):
        return self._record(request, runtime, context, final=False)

    def _inspection_submit_records(self, request, runtime, context):
        return self._record(request, runtime, context, final=True)

    def _inspection_ai_suggest(self, request: InspectionAiSuggestInput, runtime, context):
        write = self._write(runtime)
        command = AddAiSuggestionCommand(
            request.point,
            request.finding,
            request.severity,
            request.model,
            confirmation_token=write.confirmation_token,
            approval_ref=write.approval_ref,
        )
        task = self._tasks.add_ai_suggestion(
            request.task_id, command, context, idempotency_key=write.idempotency_key
        )
        return {
            "task": _brief(task),
            "pending_confirm": True,
            "idempotency_key": write.idempotency_key,
        }

    def _security_event_create(self, request: SecurityEventCreateInput, runtime, context):
        write = self._write(runtime)
        normalized = normalize_security_event(request.description, request.risk_level)
        command = CreateSecurityEventCommand(
            request.source_task_id,
            normalized.event_type,
            normalized.risk_level,
            request.location,
            request.description,
            write.confirmation_token,
            report_source="AI",
            approval_ref=write.approval_ref,
        )
        event = self._events.create_event(command, context, idempotency_key=write.idempotency_key)
        return {
            "event": _brief(event),
            "handover_required": event.risk_level == EventRiskLevel.HIGH_RISK,
            "idempotency_key": write.idempotency_key,
        }

    def _security_event_submit_disposal(self, request: SecurityDisposalInput, runtime, context):
        write = self._write(runtime)
        command = ExecuteEventActionCommand(
            EventAction.SUBMIT_DISPOSAL,
            request.expected_version,
            note=request.note,
            confirmation_token=write.confirmation_token,
            approval_ref=write.approval_ref,
        )
        event = self._events.execute_event_action(
            request.event_id, command, context, idempotency_key=write.idempotency_key
        )
        return {"event": _brief(event), "idempotency_key": write.idempotency_key}

    @staticmethod
    def _write(runtime: CapabilityRuntimeContext):
        if runtime.write is None:
            raise RuntimeError("inspection write requires server write context")
        return runtime.write
