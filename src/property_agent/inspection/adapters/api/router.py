from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from property_agent.inspection.adapters.api.dependencies import (
    get_event_service,
    get_request_context,
    get_task_service,
)
from property_agent.inspection.adapters.api.schemas import (
    AddAiSuggestionRequest,
    AddTaskRecordRequest,
    AssignEventRequest,
    AssignTaskRequest,
    ConfirmAiSuggestionsRequest,
    CreateInspectionTaskRequest,
    CreateSecurityEventRequest,
    Envelope,
    ReturnEventRequest,
    SubmitDisposalRequest,
    SubmitTaskRecordsRequest,
    VersionedActionRequest,
)
from property_agent.inspection.adapters.presentation import (
    event_data,
    task_data,
    timeline_entry_data,
)
from property_agent.inspection.application.commands import (
    AddAiSuggestionCommand,
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
from property_agent.inspection.domain.enums import EventAction, TaskAction

task_router = APIRouter(prefix="/api/inspection-tasks", tags=["inspection-task"])
event_router = APIRouter(prefix="/api/security-events", tags=["security-event"])

TaskServiceDep = Annotated[InspectionTaskService, Depends(get_task_service)]
EventServiceDep = Annotated[SecurityEventService, Depends(get_event_service)]
ContextDep = Annotated[RequestContext, Depends(get_request_context)]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _success(data, context: RequestContext) -> Envelope:
    return Envelope(success=True, data=data, error=None, request_id=context.request_id)


# ============================== 巡检任务 ==============================
@task_router.post("", response_model=Envelope, status_code=201)
def create_task(
    payload: CreateInspectionTaskRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    task = service.create_task(
        CreateInspectionTaskCommand(
            title=payload.title,
            description=payload.description,
            route_points=tuple(payload.route_points),
            planned_at=payload.planned_at,
            due_at=payload.due_at,
            attachment_ids=tuple(payload.attachment_ids),
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return _success(task_data(task, service, context), context)


@task_router.get("", response_model=Envelope)
def search_tasks(
    service: TaskServiceDep,
    context: ContextDep,
    status: Annotated[list[str] | None, Query()] = None,
    assigned_to_me: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope:
    results = service.search_tasks(
        InspectionTaskSearch(
            statuses=tuple(status or ()), assigned_to_me=assigned_to_me, limit=limit, offset=offset
        ),
        context,
    )
    return _success(
        {
            "items": [task_data(t, service, context) for t in results],
            "limit": limit,
            "offset": offset,
        },
        context,
    )


@task_router.get("/{task_id}", response_model=Envelope)
def get_task(task_id: UUID, service: TaskServiceDep, context: ContextDep) -> Envelope:
    return _success(task_data(service.get_task(task_id, context), service, context), context)


@task_router.get("/{task_id}/timeline", response_model=Envelope)
def get_task_timeline(task_id: UUID, service: TaskServiceDep, context: ContextDep) -> Envelope:
    return _success(
        [timeline_entry_data(e) for e in service.task_timeline(task_id, context)], context
    )


@task_router.post("/{task_id}/actions/assign", response_model=Envelope)
def assign_task(
    task_id: UUID,
    payload: AssignTaskRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_task(
        task_id,
        ExecuteTaskActionCommand(
            action=TaskAction.ASSIGN,
            expected_version=payload.expected_version,
            assignee_id=payload.assignee_id,
        ),
        idempotency_key,
        service,
        context,
    )


@task_router.post("/{task_id}/actions/start", response_model=Envelope)
def start_task(
    task_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_task(
        task_id,
        ExecuteTaskActionCommand(
            action=TaskAction.START, expected_version=payload.expected_version
        ),
        idempotency_key,
        service,
        context,
    )


@task_router.post("/{task_id}/actions/submit-records", response_model=Envelope)
def submit_records(
    task_id: UUID,
    payload: SubmitTaskRecordsRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_task(
        task_id,
        ExecuteTaskActionCommand(
            action=TaskAction.SUBMIT_RECORDS,
            expected_version=payload.expected_version,
            record_type=payload.record_type,
            point=payload.point,
            note=payload.note,
            confirmation_token=payload.confirmation_token,
            supplement_reason=payload.supplement_reason,
            attachment_ids=tuple(payload.attachment_ids),
        ),
        idempotency_key,
        service,
        context,
    )


@task_router.post("/{task_id}/actions/add-record", response_model=Envelope)
def add_record(
    task_id: UUID,
    payload: AddTaskRecordRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_task(
        task_id,
        ExecuteTaskActionCommand(
            action=TaskAction.ADD_RECORD,
            expected_version=payload.expected_version,
            record_type=payload.record_type,
            point=payload.point,
            note=payload.note,
            attachment_ids=tuple(payload.attachment_ids),
            is_supplement=payload.is_supplement,
            actual_time=payload.actual_time,
            supplement_reason=payload.supplement_reason,
        ),
        idempotency_key,
        service,
        context,
    )


@task_router.post("/{task_id}/actions/complete", response_model=Envelope)
def complete_task(
    task_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_task(
        task_id,
        ExecuteTaskActionCommand(
            action=TaskAction.COMPLETE, expected_version=payload.expected_version
        ),
        idempotency_key,
        service,
        context,
    )


@task_router.post("/{task_id}/ai-suggestions", response_model=Envelope, status_code=201)
def add_ai_suggestion(
    task_id: UUID,
    payload: AddAiSuggestionRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    task = service.add_ai_suggestion(
        task_id,
        AddAiSuggestionCommand(
            point=payload.point,
            finding=payload.finding,
            severity=payload.severity,
            model=payload.model,
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return _success(task_data(task, service, context), context)


@task_router.post("/{task_id}/actions/confirm-ai", response_model=Envelope)
def confirm_ai_suggestions(
    task_id: UUID,
    payload: ConfirmAiSuggestionsRequest,
    idempotency_key: IdempotencyHeader,
    service: TaskServiceDep,
    context: ContextDep,
) -> Envelope:
    task = service.confirm_ai_suggestions(task_id, context, idempotency_key=idempotency_key)
    return _success(task_data(task, service, context), context)


def _execute_task(
    task_id: UUID,
    command: ExecuteTaskActionCommand,
    idempotency_key: str,
    service: InspectionTaskService,
    context: RequestContext,
) -> Envelope:
    result = service.execute_task_action(task_id, command, context, idempotency_key=idempotency_key)
    return _success(task_data(result, service, context), context)


# ============================== 安防事件 ==============================
@event_router.post("", response_model=Envelope, status_code=201)
def create_event(
    payload: CreateSecurityEventRequest,
    idempotency_key: IdempotencyHeader,
    service: EventServiceDep,
    context: ContextDep,
) -> Envelope:
    event = service.create_event(
        CreateSecurityEventCommand(
            source_task_id=payload.source_task_id,
            event_type=payload.event_type,
            risk_level=payload.risk_level,
            location=payload.location,
            description=payload.description,
            confirmation_token=payload.confirmation_token,
            report_source=payload.report_source,
            attachment_ids=tuple(payload.attachment_ids),
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return _success(event_data(event, service, context), context)


@event_router.get("", response_model=Envelope)
def search_events(
    service: EventServiceDep,
    context: ContextDep,
    status: Annotated[list[str] | None, Query()] = None,
    risk_level: Annotated[list[str] | None, Query(alias="risk_level")] = None,
    assigned_to_me: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope:
    results = service.search_events(
        SecurityEventSearch(
            statuses=tuple(status or ()),
            risk_levels=tuple(risk_level or ()),
            assigned_to_me=assigned_to_me,
            limit=limit,
            offset=offset,
        ),
        context,
    )
    return _success(
        {
            "items": [event_data(e, service, context) for e in results],
            "limit": limit,
            "offset": offset,
        },
        context,
    )


@event_router.get("/{event_id}", response_model=Envelope)
def get_event(event_id: UUID, service: EventServiceDep, context: ContextDep) -> Envelope:
    return _success(event_data(service.get_event(event_id, context), service, context), context)


@event_router.get("/{event_id}/timeline", response_model=Envelope)
def get_event_timeline(event_id: UUID, service: EventServiceDep, context: ContextDep) -> Envelope:
    return _success(
        [timeline_entry_data(e) for e in service.event_timeline(event_id, context)], context
    )


@event_router.post("/{event_id}/actions/assign", response_model=Envelope)
def assign_event(
    event_id: UUID,
    payload: AssignEventRequest,
    idempotency_key: IdempotencyHeader,
    service: EventServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_event(
        event_id,
        ExecuteEventActionCommand(
            action=EventAction.ASSIGN,
            expected_version=payload.expected_version,
            assignee_id=payload.assignee_id,
        ),
        idempotency_key,
        service,
        context,
    )


@event_router.post("/{event_id}/actions/submit-disposal", response_model=Envelope)
def submit_disposal(
    event_id: UUID,
    payload: SubmitDisposalRequest,
    idempotency_key: IdempotencyHeader,
    service: EventServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_event(
        event_id,
        ExecuteEventActionCommand(
            action=EventAction.SUBMIT_DISPOSAL,
            expected_version=payload.expected_version,
            note=payload.note,
            attachment_ids=tuple(payload.attachment_ids),
        ),
        idempotency_key,
        service,
        context,
    )


@event_router.post("/{event_id}/actions/review-pass", response_model=Envelope)
def review_pass(
    event_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: EventServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_event(
        event_id,
        ExecuteEventActionCommand(
            action=EventAction.REVIEW_PASS, expected_version=payload.expected_version
        ),
        idempotency_key,
        service,
        context,
    )


@event_router.post("/{event_id}/actions/grade-confirm", response_model=Envelope)
def grade_confirm(
    event_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: EventServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_event(
        event_id,
        ExecuteEventActionCommand(
            action=EventAction.GRADE_CONFIRM, expected_version=payload.expected_version
        ),
        idempotency_key,
        service,
        context,
    )


@event_router.post("/{event_id}/actions/return", response_model=Envelope)
def return_event(
    event_id: UUID,
    payload: ReturnEventRequest,
    idempotency_key: IdempotencyHeader,
    service: EventServiceDep,
    context: ContextDep,
) -> Envelope:
    return _execute_event(
        event_id,
        ExecuteEventActionCommand(
            action=EventAction.RETURN, expected_version=payload.expected_version, note=payload.note
        ),
        idempotency_key,
        service,
        context,
    )


def _execute_event(
    event_id: UUID,
    command: ExecuteEventActionCommand,
    idempotency_key: str,
    service: SecurityEventService,
    context: RequestContext,
) -> Envelope:
    result = service.execute_event_action(
        event_id, command, context, idempotency_key=idempotency_key
    )
    return _success(event_data(result, service, context), context)
