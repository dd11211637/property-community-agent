"""
Production implementations of the repair module's shared ports — PRD 6.1.

Until now ``RepairUnitOfWork`` only had Protocol declarations plus test fakes,
which is why the assembled application answered ``503 ADAPTER_NOT_CONFIGURED``.
This module provides the real, database-backed adapters:

===================  =========================================================
Port                 Backing implementation
===================  =========================================================
IdempotencyPort      ``idempotency_records`` table (two-phase get / add)
ConfirmationPort     ``platform.ConfirmationService`` (token consume)
HouseAccessPort      ``user_house_bindings`` + ``houses`` (community scoped)
StaffDirectoryPort   ``user_roles`` + ``users`` (REPAIR_WORKER / duty staff)
AttachmentPort       ``attachments`` (owner, scope, status, type, size)
AuditPort            ``platform.AuditService`` (auto masking)
MessagePort          ``platform.MessageOutboxService`` (outbox, deduplicated)
HandoverPort         ``handover_tickets`` (manual takeover)
===================  =========================================================

Every adapter shares the *same* SQLAlchemy ``Session`` as the work-order
repository, so a single ``uow.commit()`` atomically persists the work order,
its timeline, the audit trail and the outbox message. Platform exceptions are
translated into repair ``BusinessError`` values so the API keeps emitting the
unified error envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.platform.application.approval_service import ApprovalService
from property_agent.platform.application.audit_service import AuditService
from property_agent.platform.infrastructure.orm_models import (
    ATTACHMENT_ALLOWED_CONTENT_TYPES,
    ATTACHMENT_MAX_SIZE_BYTES,
    AttachmentModel,
    HandoverTicketModel,
    HouseModel,
    IdempotencyRecordModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import MessageOutboxService
from property_agent.repair.application.ports import IdempotencyRecord
from property_agent.repair.domain.errors import BusinessError, forbidden, validation_error
from property_agent.repair.infrastructure.uow import SharedPorts

# Roles that may act across every house of a community.
COMMUNITY_WIDE_ROLES = frozenset({"CUSTOMER_SERVICE", "MANAGER", "SYSTEM_ADMIN"})
# Roles notified when a high-risk report is handed over.
DUTY_ROLES = ("CUSTOMER_SERVICE", "MANAGER")

MAX_ATTACHMENTS_PER_REQUEST = 9

# Human-readable message templates for the station-message outbox.
_MESSAGE_TEMPLATES: dict[str, tuple[str, str]] = {
    "ASSIGN": ("新的报修工单待接单", "有一条报修工单已派给你，请尽快接单处理。"),
    "SUBMIT_COMPLETION": ("报修已完工待验收", "你的报修已提交完工，请及时验收。"),
    "SUBMIT_REWORK_COMPLETION": ("返工已完成待验收", "返工处理已提交完工，请及时验收。"),
    "REQUEST_REWORK": ("报修工单被要求返工", "验收未通过，请查看返工要求并重新处理。"),
    "HIGH_RISK_HANDOVER": (
        "高风险报修待人工接管",
        "收到一条高风险报修，已创建人工接管单，请立即联系住户并现场核实。",
    ),
}


def _template(event_type: str) -> tuple[str, str]:
    return _MESSAGE_TEMPLATES.get(
        event_type, (f"报修工单事件 {event_type}", f"报修工单发生事件：{event_type}。")
    )


# ═══════════════════════════════════════════════════════════════
# IdempotencyPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyIdempotencyPort:
    """Two-phase idempotency using the shared ``idempotency_records`` table.

    ``get`` is side-effect free (the service decides whether it is a replay or
    a conflict); ``add`` writes the record together with the response snapshot
    inside the same transaction as the business write. That combination is what
    makes "first call timed out but actually succeeded → retry returns the same
    work order" work (PRD 12.3).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, actor_id: UUID, operation: str, key: str) -> IdempotencyRecord | None:
        record = self._session.execute(
            select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.actor_id == actor_id,
                IdempotencyRecordModel.operation == operation,
                IdempotencyRecordModel.key == key,
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        if record.resource_id is None or record.response_snapshot is None:
            # A previous attempt registered the key but never completed; treat
            # it as absent so the caller retries rather than replaying a
            # half-written response.
            return None
        return IdempotencyRecord(
            actor_id=record.actor_id,
            operation=record.operation,
            key=record.key,
            request_hash=record.request_hash,
            resource_id=UUID(record.resource_id),
            response_snapshot=dict(record.response_snapshot),
        )

    def add(self, record: IdempotencyRecord) -> None:
        existing = self._session.execute(
            select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.actor_id == record.actor_id,
                IdempotencyRecordModel.operation == record.operation,
                IdempotencyRecordModel.key == record.key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.request_hash = record.request_hash
            existing.resource_id = str(record.resource_id)
            existing.response_snapshot = record.response_snapshot
            return
        self._session.add(
            IdempotencyRecordModel(
                actor_id=record.actor_id,
                operation=record.operation,
                key=record.key,
                request_hash=record.request_hash,
                resource_id=str(record.resource_id),
                response_snapshot=record.response_snapshot,
            )
        )
        self._session.flush()


# ═══════════════════════════════════════════════════════════════
# ConfirmationPort — 复用 platform.application.PlatformConfirmationPort
# （P0 原子化消费：审批 + 令牌在同一 UoW session 内）
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# HouseAccessPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyHouseAccessPort:
    """Verify the actor may act on a house inside the current community.

    Order of checks matters: the house must belong to the community first
    (cross-community access is rejected even for staff), then residents must
    hold an ACTIVE binding to that specific house.

    Roles are re-read from the database rather than taken from the request
    context — a token replayed after a role was revoked must not pass.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_access(
        self, *, actor_id: UUID, community_id: UUID, house_id: UUID, request_id: str
    ) -> None:
        house = self._session.execute(
            select(HouseModel).where(HouseModel.id == house_id)
        ).scalar_one_or_none()
        if house is None or house.community_id != community_id:
            raise forbidden("The house does not exist in the current community.")

        if self._has_community_wide_role(actor_id, community_id):
            return

        binding = self._session.execute(
            select(UserHouseBindingModel).where(
                UserHouseBindingModel.user_id == actor_id,
                UserHouseBindingModel.house_id == house_id,
                UserHouseBindingModel.status == "ACTIVE",
            )
        ).scalar_one_or_none()
        if binding is None:
            raise forbidden("You are not bound to this house.")

    def _has_community_wide_role(self, actor_id: UUID, community_id: UUID) -> bool:
        found = self._session.execute(
            select(UserRoleModel.id)
            .join(UserModel, UserModel.id == UserRoleModel.user_id)
            .where(
                UserRoleModel.user_id == actor_id,
                UserModel.community_id == community_id,
                UserModel.status == "ACTIVE",
                UserRoleModel.role.in_(tuple(COMMUNITY_WIDE_ROLES)),
            )
        ).first()
        return found is not None


# ═══════════════════════════════════════════════════════════════
# StaffDirectoryPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyStaffDirectoryPort:
    """Look up staff by role within a community."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_repair_worker(self, *, user_id: UUID, community_id: UUID, request_id: str) -> None:
        found = self._session.execute(
            select(UserModel.id)
            .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
            .where(
                UserModel.id == user_id,
                UserModel.community_id == community_id,
                UserModel.status == "ACTIVE",
                UserRoleModel.role == "REPAIR_WORKER",
            )
        ).first()
        if found is None:
            raise validation_error(
                "The assignee is not an active repair worker in this community.",
                assignee_id=str(user_id),
            )

    def list_duty_staff(self, *, community_id: UUID, request_id: str) -> tuple[UUID, ...]:
        rows = (
            self._session.execute(
                select(UserModel.id)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .where(
                    UserModel.community_id == community_id,
                    UserModel.status == "ACTIVE",
                    UserRoleModel.role.in_(DUTY_ROLES),
                )
                .distinct()
            )
            .scalars()
            .all()
        )
        return tuple(rows)


# ═══════════════════════════════════════════════════════════════
# AttachmentPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyAttachmentPort:
    """Validate attachment ownership, scope, upload status, type and size.

    PRD 6.1 explicitly requires all four dimensions. A client can only pass
    attachment IDs, so every one of them is re-read from the database — the
    declared metadata in the request is never trusted.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_usable(
        self,
        *,
        attachment_ids: tuple[UUID, ...],
        actor_id: UUID,
        community_id: UUID,
        request_id: str,
    ) -> None:
        if not attachment_ids:
            return
        if len(set(attachment_ids)) != len(attachment_ids):
            raise validation_error("Duplicate attachment IDs are not allowed.")
        if len(attachment_ids) > MAX_ATTACHMENTS_PER_REQUEST:
            raise validation_error(
                f"At most {MAX_ATTACHMENTS_PER_REQUEST} attachments are allowed.",
                attachment_count=len(attachment_ids),
            )

        rows = (
            self._session.execute(
                select(AttachmentModel).where(AttachmentModel.id.in_(attachment_ids))
            )
            .scalars()
            .all()
        )
        found = {row.id: row for row in rows}

        missing = [str(item) for item in attachment_ids if item not in found]
        if missing:
            raise validation_error("Some attachments do not exist.", attachment_ids=missing)

        for attachment_id in attachment_ids:
            attachment = found[attachment_id]
            if attachment.community_id != community_id:
                raise forbidden("The attachment belongs to another community.")
            if attachment.uploader_id != actor_id:
                raise forbidden("You can only attach files that you uploaded.")
            if attachment.status != "UPLOADED":
                raise validation_error(
                    "The attachment upload has not completed.",
                    attachment_id=str(attachment_id),
                    status=attachment.status,
                )
            if attachment.content_type not in ATTACHMENT_ALLOWED_CONTENT_TYPES:
                raise validation_error(
                    "The attachment type is not supported.",
                    attachment_id=str(attachment_id),
                    content_type=attachment.content_type,
                    allowed=sorted(ATTACHMENT_ALLOWED_CONTENT_TYPES),
                )
            if attachment.size_bytes > ATTACHMENT_MAX_SIZE_BYTES:
                raise validation_error(
                    "The attachment exceeds the maximum allowed size.",
                    attachment_id=str(attachment_id),
                    size_bytes=attachment.size_bytes,
                    max_size_bytes=ATTACHMENT_MAX_SIZE_BYTES,
                )


# ═══════════════════════════════════════════════════════════════
# AuditPort
# ═══════════════════════════════════════════════════════════════


class PlatformAuditPort:
    """Write audit rows through the platform service (sensitive data masked)."""

    def __init__(self, session: Session) -> None:
        self._service = AuditService(session)

    def add(
        self,
        *,
        community_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        parameter_summary: dict[str, Any],
        request_id: str,
        created_at: datetime,
    ) -> None:
        self._service.log(
            actor_id=actor_id,
            community_id=community_id,
            action=f"REPAIR_{action}",
            resource_type=resource_type,
            resource_id=str(resource_id),
            parameter_summary=parameter_summary,
            result="SUCCESS",
            request_id=request_id,
        )


# ═══════════════════════════════════════════════════════════════
# MessagePort
# ═══════════════════════════════════════════════════════════════


class PlatformMessagePort:
    """Enqueue station messages into the transactional outbox.

    The idempotency key is derived from (resource, event, receiver, request) so
    a retried API call never produces duplicate notifications, while a genuinely
    new state transition always does.
    """

    def __init__(self, session: Session) -> None:
        self._service = MessageOutboxService(session)

    def enqueue(
        self,
        *,
        community_id: UUID,
        receiver_id: UUID,
        event_type: str,
        resource_id: UUID,
        request_id: str,
        created_at: datetime,
    ) -> None:
        title, body = _template(event_type)
        self._service.enqueue(
            receiver_id=receiver_id,
            business_type="REPAIR",
            resource_id=str(resource_id),
            title=title,
            body=body,
            idempotency_key=f"REPAIR:{resource_id}:{event_type}:{receiver_id}:{request_id}",
        )


# ═══════════════════════════════════════════════════════════════
# HandoverPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyHandoverPort:
    """Create manual-handover tickets for cases the system must not auto-handle."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        community_id: UUID,
        requester_id: UUID,
        queue: str,
        reason: str,
        summary: str,
        payload: dict[str, Any],
        request_id: str,
        created_at: datetime,
    ) -> UUID:
        ticket = HandoverTicketModel(
            community_id=community_id,
            requester_id=requester_id,
            source="REPAIR",
            queue=queue,
            summary=summary,
            reason=reason,
            status="PENDING",
            resource_type="WORK_ORDER",
            resource_id=None,
            request_id=request_id,
            payload=payload,
        )
        self._session.add(ticket)
        self._session.flush()
        return ticket.id


# ═══════════════════════════════════════════════════════════════
# Assembly
# ═══════════════════════════════════════════════════════════════


def build_shared_ports(
    session: Session, approval_service: ApprovalService, *, enforce_fence: bool = False
) -> SharedPorts:
    """Create every production shared port bound to one SQLAlchemy session.

    ``approval_service`` 由容器装配后传入；端口内部用它做 P0 审批原子消费。
    ``enforce_fence`` 由生产容器注入（= settings.agent_concurrency_guard），开启时
    缺失 lease 的受控写会被端口拒绝（fencing 失败关闭）。
    """
    from property_agent.platform.application.platform_confirmation_port import (
        PlatformConfirmationPort,
    )

    return SharedPorts(
        idempotency=SqlAlchemyIdempotencyPort(session),
        confirmations=PlatformConfirmationPort(
            session,
            approval_service,
            error_factory=BusinessError,
            enforce_fence=enforce_fence,
        ),
        house_access=SqlAlchemyHouseAccessPort(session),
        staff_directory=SqlAlchemyStaffDirectoryPort(session),
        attachments=SqlAlchemyAttachmentPort(session),
        audit=PlatformAuditPort(session),
        messages=PlatformMessagePort(session),
        handover=SqlAlchemyHandoverPort(session),
    )
