"""Authenticated message-center and management dashboard endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from property_agent.platform.adapters.api.dependencies import (
    RequestContext,
    get_current_user,
    require_idempotency_key,
    require_role,
)
from property_agent.platform.adapters.api.schemas import Envelope, StaffOptionResponse
from property_agent.platform.application.message_admin_service import (
    AdminDashboardService,
    MessageCenterService,
)
from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.infrastructure.orm_models import UserModel, UserRoleModel
from property_agent.platform.schemas import Envelope as TypedEnvelope

router = APIRouter(tags=["platform-operations"])

MessageStatus = Literal["PENDING", "SENT", "FAILED", "READ", "UNREAD"]
BusinessType = Literal["REPAIR", "ANNOUNCEMENT", "BILLING", "INSPECTION"]
StaffRole = Literal["REPAIR_WORKER", "SECURITY_GUARD"]
StaffDirectoryContext = Annotated[
    RequestContext,
    Depends(require_role("CUSTOMER_SERVICE", "MANAGER", "SYSTEM_ADMIN", "SECURITY_GUARD")),
]


@router.get("/api/staff", response_model=TypedEnvelope[list[StaffOptionResponse]])
def list_staff(
    role: StaffRole,
    context: StaffDirectoryContext,
    db: Session = Depends(get_db),  # noqa: B008
) -> TypedEnvelope[list[StaffOptionResponse]]:
    """Return active, currently authorized staff from the caller's community."""
    now = datetime.now(timezone.utc)
    rows = (
        db.query(UserModel, UserRoleModel)
        .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
        .filter(
            UserModel.community_id == context.community_id,
            UserModel.status == "ACTIVE",
            UserRoleModel.role == role,
            UserRoleModel.valid_from <= now,
            (UserRoleModel.valid_until.is_(None) | (UserRoleModel.valid_until > now)),
        )
        .order_by(UserModel.display_name, UserModel.id)
        .all()
    )
    data = [
        StaffOptionResponse(id=user.id, display_name=user.display_name, role=assignment.role)
        for user, assignment in rows
    ]
    return TypedEnvelope(success=True, data=data, request_id=context.request_id)


@router.get("/api/messages", response_model=Envelope)
def list_messages(
    status: MessageStatus | None = None,
    business_type: BusinessType | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    context: RequestContext = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Envelope:
    data = MessageCenterService(db).list_messages(
        context,
        status=status,
        business_type=business_type,
        limit=limit,
        offset=offset,
    )
    return Envelope(data=data, request_id=context.request_id)


@router.post("/api/messages/read-all", response_model=Envelope)
def mark_all_messages_read(
    context: RequestContext = Depends(get_current_user),  # noqa: B008
    idempotency_key: str = Depends(require_idempotency_key),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Envelope:
    data = MessageCenterService(db).mark_all_read(context, idempotency_key=idempotency_key)
    return Envelope(data=data, request_id=context.request_id)


@router.post("/api/messages/{message_id}/read", response_model=Envelope)
def mark_message_read(
    message_id: UUID,
    context: RequestContext = Depends(get_current_user),  # noqa: B008
    idempotency_key: str = Depends(require_idempotency_key),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Envelope:
    data = MessageCenterService(db).mark_read(
        context,
        message_id=message_id,
        idempotency_key=idempotency_key,
    )
    return Envelope(data=data, request_id=context.request_id)


@router.get("/api/admin/dashboard", response_model=Envelope)
def admin_dashboard(
    context: RequestContext = Depends(require_role("MANAGER", "SYSTEM_ADMIN")),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Envelope:
    data = AdminDashboardService(db).get_dashboard(context)
    return Envelope(data=data, request_id=context.request_id)
