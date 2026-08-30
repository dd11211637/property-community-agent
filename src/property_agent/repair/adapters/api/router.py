from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.schemas import Envelope
from property_agent.repair.adapters.api.dependencies import get_request_context, get_service
from property_agent.repair.adapters.api.response_schemas import (
    WorkOrderEnvelope,
    WorkOrderListEnvelope,
    WorkOrderTimelineEnvelope,
)
from property_agent.repair.adapters.api.schemas import (
    AssignRequest,
    CompletionRequest,
    CreateWorkOrderRequest,
    ProgressRequest,
    RejectRequest,
    ReviewRequest,
    ReworkRequest,
    VersionedActionRequest,
)
from property_agent.repair.adapters.api.work_order_presenter import WorkOrderPresenter
from property_agent.repair.adapters.presentation import timeline_entry_data
from property_agent.repair.application.commands import (
    CreateReviewCommand,
    CreateWorkOrderCommand,
    ExecuteActionCommand,
    WorkOrderSearch,
)
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.enums import ActionCode

router = APIRouter(prefix="/api/work-orders", tags=["repair"])
ServiceDependency = Annotated[WorkOrderService, Depends(get_service)]
ContextDependency = Annotated[RequestContext, Depends(get_request_context)]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def get_work_order_presenter(db: Session = Depends(get_db)) -> WorkOrderPresenter:  # noqa: B008
    return WorkOrderPresenter(db)


PresenterDependency = Annotated[WorkOrderPresenter, Depends(get_work_order_presenter)]


def _success(data, context: RequestContext) -> Envelope:
    return Envelope(success=True, data=data, error=None, request_id=context.request_id)


@router.post("", response_model=WorkOrderEnvelope, status_code=201)
def create_work_order(
    payload: CreateWorkOrderRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    work_order = service.create(
        CreateWorkOrderCommand(
            house_id=payload.house_id,
            category=payload.category,
            location=payload.location,
            description=payload.description,
            urgency=payload.urgency,
            confirmation_token=payload.confirmation_token,
            attachment_ids=tuple(payload.attachment_ids),
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return _success(presenter.present(work_order, service, context), context)


@router.get("", response_model=WorkOrderListEnvelope)
def search_work_orders(
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
    house_id: UUID | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    assigned_to_me: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope:
    results = service.search(
        WorkOrderSearch(
            house_id=house_id,
            statuses=tuple(status or ()),
            assigned_to_me=assigned_to_me,
            limit=limit,
            offset=offset,
        ),
        context,
    )
    return _success(
        {
            "items": presenter.present_many(results, service, context),
            "limit": limit,
            "offset": offset,
        },
        context,
    )


@router.get("/{work_order_id}", response_model=WorkOrderEnvelope)
def get_work_order(
    work_order_id: UUID,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    result = service.get(work_order_id, context)
    return _success(presenter.present(result, service, context), context)


@router.get("/{work_order_id}/timeline", response_model=WorkOrderTimelineEnvelope)
def get_work_order_timeline(
    work_order_id: UUID,
    service: ServiceDependency,
    context: ContextDependency,
) -> Envelope:
    result = service.timeline(work_order_id, context)
    return _success([timeline_entry_data(item) for item in result], context)


def _execute(
    work_order_id: UUID,
    command: ExecuteActionCommand,
    idempotency_key: str,
    service: WorkOrderService,
    context: RequestContext,
    presenter: WorkOrderPresenter,
) -> Envelope:
    result = service.execute_action(
        work_order_id, command, context, idempotency_key=idempotency_key
    )
    return _success(presenter.present(result, service, context), context)


@router.post("/{work_order_id}/actions/assign", response_model=WorkOrderEnvelope)
def assign_work_order(
    work_order_id: UUID,
    payload: AssignRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    return _execute(
        work_order_id,
        ExecuteActionCommand(
            action=ActionCode.ASSIGN,
            expected_version=payload.expected_version,
            assignee_id=payload.assignee_id,
        ),
        idempotency_key,
        service,
        context,
        presenter,
    )


@router.post("/{work_order_id}/actions/accept", response_model=WorkOrderEnvelope)
def accept_work_order(
    work_order_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    return _execute(
        work_order_id,
        ExecuteActionCommand(action=ActionCode.ACCEPT, expected_version=payload.expected_version),
        idempotency_key,
        service,
        context,
        presenter,
    )


@router.post("/{work_order_id}/actions/reject", response_model=WorkOrderEnvelope)
def reject_work_order(
    work_order_id: UUID,
    payload: RejectRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    return _execute(
        work_order_id,
        ExecuteActionCommand(
            action=ActionCode.REJECT,
            expected_version=payload.expected_version,
            reason=payload.reason,
        ),
        idempotency_key,
        service,
        context,
        presenter,
    )


@router.post("/{work_order_id}/actions/record-progress", response_model=WorkOrderEnvelope)
def record_progress(
    work_order_id: UUID,
    payload: ProgressRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    return _execute(
        work_order_id,
        ExecuteActionCommand(
            action=ActionCode.RECORD_PROGRESS,
            expected_version=payload.expected_version,
            note=payload.note,
            record_type=payload.record_type,
            appointment_at=payload.appointment_at,
            attachment_ids=tuple(payload.attachment_ids),
        ),
        idempotency_key,
        service,
        context,
        presenter,
    )


@router.post("/{work_order_id}/actions/submit-completion", response_model=WorkOrderEnvelope)
def submit_completion(
    work_order_id: UUID,
    payload: CompletionRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    current = service.get(work_order_id, context)
    action = (
        ActionCode.SUBMIT_REWORK_COMPLETION
        if current.status.value == "REWORKING"
        else ActionCode.SUBMIT_COMPLETION
    )
    return _execute(
        work_order_id,
        ExecuteActionCommand(
            action=action,
            expected_version=payload.expected_version,
            note=payload.note,
            attachment_ids=tuple(payload.attachment_ids),
        ),
        idempotency_key,
        service,
        context,
        presenter,
    )


@router.post("/{work_order_id}/actions/verify-pass", response_model=WorkOrderEnvelope)
def verify_pass(
    work_order_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    return _execute(
        work_order_id,
        ExecuteActionCommand(
            action=ActionCode.VERIFY_PASS, expected_version=payload.expected_version
        ),
        idempotency_key,
        service,
        context,
        presenter,
    )


@router.post("/{work_order_id}/actions/request-rework", response_model=WorkOrderEnvelope)
def request_rework(
    work_order_id: UUID,
    payload: ReworkRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    return _execute(
        work_order_id,
        ExecuteActionCommand(
            action=ActionCode.REQUEST_REWORK,
            expected_version=payload.expected_version,
            reason=payload.reason,
        ),
        idempotency_key,
        service,
        context,
        presenter,
    )


@router.post("/{work_order_id}/reviews", response_model=WorkOrderEnvelope, status_code=201)
def create_review(
    work_order_id: UUID,
    payload: ReviewRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDependency,
    context: ContextDependency,
    presenter: PresenterDependency,
) -> Envelope:
    result = service.create_review(
        work_order_id,
        CreateReviewCommand(rating=payload.rating, comment=payload.comment),
        context,
        idempotency_key=idempotency_key,
    )
    return _success(presenter.present(result, service, context), context)
