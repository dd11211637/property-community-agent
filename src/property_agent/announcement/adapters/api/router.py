from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from property_agent.announcement.adapters.api.dependencies import (
    get_announcement_service,
    get_request_context,
)
from property_agent.announcement.adapters.api.schemas import (
    CreateAnnouncementRequest,
    EditAnnouncementRequest,
    PublishAnnouncementRequest,
    RejectAnnouncementRequest,
    ScheduleAnnouncementRequest,
    VersionedActionRequest,
    WithdrawAnnouncementRequest,
)
from property_agent.announcement.adapters.presentation import (
    announcement_data,
    audience_snapshot_data,
    version_data,
)
from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
    EditAnnouncementCommand,
    ReviewActionCommand,
    ScheduleAnnouncementCommand,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.enums import AnnouncementAction
from property_agent.platform.context import RequestContext
from property_agent.platform.responses import success_envelope
from property_agent.platform.schemas import Envelope

router = APIRouter(prefix="/api/announcements", tags=["announcement"])
ServiceDep = Annotated[AnnouncementService, Depends(get_announcement_service)]
ContextDep = Annotated[RequestContext, Depends(get_request_context)]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.post("", response_model=Envelope, status_code=201)
def create(
    payload: CreateAnnouncementRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    item = service.create_draft(
        CreateAnnouncementCommand(
            payload.title,
            payload.body,
            payload.category,
            payload.audience_condition,
            payload.scheduled_at,
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return success_envelope(announcement_data(item, service, context), context)


@router.get("", response_model=Envelope)
def search(
    service: ServiceDep,
    context: ContextDep,
    status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope:
    items = service.search(AnnouncementSearch(tuple(status or ()), limit, offset), context)
    return success_envelope(
        {
            "items": [announcement_data(item, service, context) for item in items],
            "limit": limit,
            "offset": offset,
        },
        context,
    )


@router.get("/{announcement_id}", response_model=Envelope)
def get(announcement_id: UUID, service: ServiceDep, context: ContextDep) -> Envelope:
    return success_envelope(
        announcement_data(service.get(announcement_id, context), service, context), context
    )


@router.patch("/{announcement_id}", response_model=Envelope)
def edit(
    announcement_id: UUID,
    payload: EditAnnouncementRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    item = service.edit_draft(
        announcement_id,
        EditAnnouncementCommand(
            payload.title,
            payload.body,
            payload.category,
            payload.audience_condition,
            payload.expected_version,
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return success_envelope(announcement_data(item, service, context), context)


@router.get("/{announcement_id}/audience-preview", response_model=Envelope)
def preview_audience(announcement_id: UUID, service: ServiceDep, context: ContextDep) -> Envelope:
    return success_envelope(
        audience_snapshot_data(service.preview_audience(announcement_id, context)), context
    )


@router.post("/{announcement_id}/submit-review", response_model=Envelope)
def submit_review(
    announcement_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    item = service.submit_review(
        announcement_id,
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, payload.expected_version),
        context,
        idempotency_key=idempotency_key,
    )
    return success_envelope(announcement_data(item, service, context), context)


@router.post("/{announcement_id}/actions/approve", response_model=Envelope)
def approve(
    announcement_id: UUID,
    payload: VersionedActionRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    return _review(
        announcement_id,
        AnnouncementAction.APPROVE,
        payload,
        None,
        idempotency_key,
        service,
        context,
    )


@router.post("/{announcement_id}/actions/reject", response_model=Envelope)
def reject(
    announcement_id: UUID,
    payload: RejectAnnouncementRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    return _review(
        announcement_id,
        AnnouncementAction.REJECT,
        payload,
        payload.reason,
        idempotency_key,
        service,
        context,
    )


@router.post("/{announcement_id}/actions/publish", response_model=Envelope)
def publish(
    announcement_id: UUID,
    payload: PublishAnnouncementRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    item = service.publish(
        announcement_id,
        ReviewActionCommand(
            AnnouncementAction.PUBLISH,
            payload.expected_version,
            confirmation_token=payload.confirmation_token,
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return success_envelope(announcement_data(item, service, context), context)


@router.post("/{announcement_id}/actions/schedule", response_model=Envelope)
def schedule_publish(
    announcement_id: UUID,
    payload: ScheduleAnnouncementRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    item = service.schedule_publish(
        announcement_id,
        ScheduleAnnouncementCommand(
            payload.expected_version,
            payload.scheduled_at,
            payload.confirmation_token,
        ),
        context,
        idempotency_key=idempotency_key,
    )
    return success_envelope(announcement_data(item, service, context), context)


@router.post("/{announcement_id}/actions/withdraw", response_model=Envelope)
def withdraw(
    announcement_id: UUID,
    payload: WithdrawAnnouncementRequest,
    idempotency_key: IdempotencyHeader,
    service: ServiceDep,
    context: ContextDep,
) -> Envelope:
    item = service.withdraw(
        announcement_id,
        ReviewActionCommand(AnnouncementAction.WITHDRAW, payload.expected_version, payload.reason),
        context,
        idempotency_key=idempotency_key,
    )
    return success_envelope(announcement_data(item, service, context), context)


@router.get("/{announcement_id}/versions", response_model=Envelope)
def versions(announcement_id: UUID, service: ServiceDep, context: ContextDep) -> Envelope:
    return success_envelope(
        [version_data(item) for item in service.versions(announcement_id, context)], context
    )


def _review(
    announcement_id: UUID,
    action: AnnouncementAction,
    payload: VersionedActionRequest,
    reason: str | None,
    idempotency_key: str,
    service: AnnouncementService,
    context: RequestContext,
) -> Envelope:
    item = service.review_action(
        announcement_id,
        ReviewActionCommand(action, payload.expected_version, reason),
        context,
        idempotency_key=idempotency_key,
    )
    return success_envelope(announcement_data(item, service, context), context)
