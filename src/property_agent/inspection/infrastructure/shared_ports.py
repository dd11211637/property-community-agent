"""
Production implementations of the inspection module's shared ports — PRD 6.4.

Until now ``InspectionUnitOfWork`` only had Protocol declarations plus test
fakes, which is why the assembled application answered ``503 ADAPTER_NOT_CONFIGURED``
and the high-risk "notify on-duty staff" branch had no real staff directory to
call. This module provides the real, database-backed adapters:

===================  =========================================================
Port                 Backing implementation
===================  =========================================================
IdempotencyPort      ``idempotency_records`` table (two-phase get / add)
ConfirmationPort     ``platform.ConfirmationService`` (token consume)
StaffDirectoryPort   ``user_roles`` + ``users`` (SECURITY_GUARD / duty staff)
AttachmentPort       ``attachments`` (owner, scope, status, type, size)
AuditPort            ``platform.AuditService`` (auto masking)
MessagePort          ``platform.MessageOutboxService`` (outbox, deduplicated)
EscalationPort       ``handover_tickets`` (high-risk backup contact / 升级)
===================  =========================================================

Every adapter shares the *same* SQLAlchemy ``Session`` as the inspection
repository, so a single ``uow.commit()`` atomically persists the task / event,
its timeline, the audit trail, the outbox message and (when needed) the
escalation handover ticket. Platform exceptions are translated into inspection
``BusinessError`` values so the API keeps emitting the unified error envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.inspection.application.ports import IdempotencyRecord
from property_agent.inspection.domain.errors import BusinessError, forbidden, validation_error
from property_agent.platform.application.approval_service import ApprovalError, ApprovalService
from property_agent.platform.application.audit_service import AuditService
from property_agent.platform.application.confirmation_service import ConfirmationService
from property_agent.platform.application.platform_confirmation_port import enforce_agent_fence
from property_agent.platform.domain.exceptions import InvalidConfirmationTokenException
from property_agent.platform.infrastructure.orm_models import (
    ATTACHMENT_ALLOWED_CONTENT_TYPES,
    ATTACHMENT_MAX_SIZE_BYTES,
    AttachmentModel,
    HandoverTicketModel,
    IdempotencyRecordModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import MessageOutboxService

# 安保相关角色在数据库中的字符串，与巡检领域 Role 枚举（SECURITY_STAFF）分离。
SECURITY_DB_ROLE = "SECURITY_GUARD"
DUTY_ROLES = ("SECURITY_GUARD", "MANAGER")

MAX_ATTACHMENTS_PER_REQUEST = 9

# 站内信模板（business_type=INSPECTION）。
_MESSAGE_TEMPLATES: dict[str, tuple[str, str]] = {
    "HIGH_RISK_EVENT": (
        "高风险安防事件待处置",
        "收到一条高风险安防事件，请立即到场核实并按预案处置。",
    ),
    "EVENT_ASSIGN": ("安防事件已分派给你", "有一条安防事件已分派给你，请尽快处置并上报。"),
}


def _template(event_type: str) -> tuple[str, str]:
    return _MESSAGE_TEMPLATES.get(
        event_type, (f"安防事件 {event_type}", f"安防事件发生：{event_type}。")
    )


# ═══════════════════════════════════════════════════════════════
# IdempotencyPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyIdempotencyPort:
    """Two-phase idempotency on the shared ``idempotency_records`` table."""

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
# ConfirmationPort
# ═══════════════════════════════════════════════════════════════


class PlatformConfirmationPort:
    """原子化消费确认令牌 + 审批（P0 正确性底座）。

    与 repair / announcement 同形：先消费审批（同一 UoW 内 ``FOR UPDATE``，
    校验 actor/action/params_hash 后置 ``CONSUMED``），再消费令牌作为纵深防御。
    巡检任务提交、安防事件上报等受控写操作均走这条路径。
    """

    def __init__(
        self,
        session: Session,
        approval_service: ApprovalService,
        *,
        error_factory: Any,
        enforce_fence: bool = False,
    ) -> None:
        self._session = session
        self._approval_service = approval_service
        self._service = ConfirmationService(session)
        self._error_factory = error_factory
        # 生产 fencing 失败关闭开关：开启时若当前 turn 没有有效 lease（未经 runner
        # 注入），任何业务 mutation 都禁止落地。测试环境保持 False（mock 放行）。
        self._enforce_fence = enforce_fence

    def consume(
        self,
        *,
        approval_ref: str | None,
        token: str,
        actor_id: UUID,
        action: str,
        parameter_hash: str,
        request_id: str,
    ) -> None:
        # P0-4: 在任何 mutation / 审批消费之前校验当前 turn 仍拥有 conversation
        # lease（fencing）。lease 从 trusted RequestContext 取，不由模型 slots 传入。
        enforce_agent_fence(self._session, enforce_fence=self._enforce_fence)
        if approval_ref:
            try:
                self._approval_service.consume(
                    approval_id=UUID(approval_ref),
                    actor_id=actor_id,
                    action=action,
                    params_hash=parameter_hash,
                    session=self._session,
                )
            except ApprovalError as exc:
                raise self._error_factory(
                    f"CONFIRMATION_{exc.code}",
                    exc.message,
                    exc.status_code,
                ) from exc
        if not token or not token.strip():
            raise self._error_factory(
                "CONFIRMATION_REQUIRED",
                "This operation requires a confirmation token.",
                422,
            )
        try:
            self._service.consume(
                token=token,
                actor_id=actor_id,
                action=action,
                parameter_hash=parameter_hash,
                request_id=request_id,
            )
        except InvalidConfirmationTokenException as exc:
            raise self._error_factory("CONFIRMATION_INVALID", exc.message, 422) from exc


# ═══════════════════════════════════════════════════════════════
# StaffDirectoryPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyStaffDirectoryPort:
    """安保人员目录与值班人员查询（PRD 6.4 高风险通知值班人员）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_security_staff(self, *, user_id: UUID, community_id: UUID, request_id: str) -> None:
        found = self._session.execute(
            select(UserModel.id)
            .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
            .where(
                UserModel.id == user_id,
                UserModel.community_id == community_id,
                UserModel.status == "ACTIVE",
                UserRoleModel.role == SECURITY_DB_ROLE,
            )
        ).first()
        if found is None:
            raise validation_error(
                "The assignee is not an active security staff member in this community.",
                assignee_id=str(user_id),
            )

    def list_duty_users(self, community_id: UUID) -> list[UUID]:
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
        return list(rows)


# ═══════════════════════════════════════════════════════════════
# AttachmentPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyAttachmentPort:
    """校验附件归属、上传状态、类型与大小（PRD 6.4：附件上传状态）。"""

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
            action=f"INSPECTION_{action}" if not action.startswith("INSPECTION_") else action,
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

    站内信状态（PENDING/SENT/FAILED）由 OutboxDispatcher 异步维护，与业务
    状态解耦（PRD R-04：通知失败时业务状态不受影响）。幂等键保证重试不会产生
    重复通知。
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
            business_type="INSPECTION",
            resource_id=str(resource_id),
            title=title,
            body=body,
            idempotency_key=f"INSPECTION:{resource_id}:{event_type}:{receiver_id}:{request_id}",
        )


# ═══════════════════════════════════════════════════════════════
# EscalationPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyEscalationPort:
    """高风险事件无可用值班人员时，升级到备用联系人（PRD 6.4）。

    创建一张 ``handover_tickets`` 记录（来源 INSPECTION，队列 SECURITY，
    原因 HIGH_RISK），作为人工兜底联系人。Outbox 仅在通知真正失败时把
    ``message_records.status`` 置为 FAILED；升级则是"无人可通知"这一更前置
    的失败场景的兜底。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def escalate_high_risk(
        self, *, community_id, event_id, event_business_no, reason, summary, request_id, created_at
    ) -> UUID:
        ticket = HandoverTicketModel(
            community_id=community_id,
            requester_id=None,
            resource_type="EVENT",
            resource_id=str(event_id),
            request_id=request_id,
            payload={
                "event_business_no": event_business_no,
                "reason": reason,
            },
            source="INSPECTION",
            queue="SECURITY",
            summary=summary,
            reason="HIGH_RISK",
            status="PENDING",
        )
        self._session.add(ticket)
        self._session.flush()
        return ticket.id


# ═══════════════════════════════════════════════════════════════
# Assembly
# ═══════════════════════════════════════════════════════════════


def build_inspection_ports(
    session: Session, approval_service: ApprovalService, *, enforce_fence: bool = False
):
    """Create every production shared port bound to one SQLAlchemy session."""
    from property_agent.inspection.application.ports import SharedPorts

    return SharedPorts(
        idempotency=SqlAlchemyIdempotencyPort(session),
        confirmations=PlatformConfirmationPort(
            session, approval_service, error_factory=BusinessError, enforce_fence=enforce_fence
        ),
        staff_directory=SqlAlchemyStaffDirectoryPort(session),
        attachments=SqlAlchemyAttachmentPort(session),
        audit=PlatformAuditPort(session),
        messages=PlatformMessagePort(session),
        escalation=SqlAlchemyEscalationPort(session),
    )
