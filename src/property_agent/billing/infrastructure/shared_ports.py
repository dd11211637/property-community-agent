"""
infrastructure/shared_ports.py     账单模块生产端口（PRD 6.3）

提供幂等端口（复用平台 idempotency_records 表）、审计端口（复用平台
AuditService）与原子确认端口（消费 confirmation_tokens 与 agent_action_approvals）。
与 announcement/repair 一致：所有适配器共享同一 SQLAlchemy Session，
平台异常翻译为 billing BusinessError。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.billing.application.ports import IdempotencyRecord
from property_agent.billing.errors import BillingError
from property_agent.platform.application.approval_service import ApprovalError, ApprovalService
from property_agent.platform.application.audit_service import AuditService
from property_agent.platform.application.confirmation_service import ConfirmationService
from property_agent.platform.domain.exceptions import InvalidConfirmationTokenException
from property_agent.platform.infrastructure.orm_models import IdempotencyRecordModel


class SqlAlchemyBillingIdempotencyPort:
    """两阶段幂等于平台 ``idempotency_records`` 表（与 announcement 同构）。"""

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
        if record is None or record.resource_id is None or record.response_snapshot is None:
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
            if existing.request_hash != record.request_hash:
                raise ValueError("idempotency key reused with different request hash")
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


class PlatformBillingAuditPort:
    """审计端口：写入平台 AuditService（敏感字段自动脱敏）。"""

    def __init__(self, session: Session) -> None:
        self._service = AuditService(session)

    def add(self, **event: Any) -> None:
        action = str(event["action"])
        self._service.log(
            actor_id=event["actor_id"],
            community_id=event["community_id"],
            action=action if action.startswith("BILLING") else f"BILLING_{action}",
            resource_type=str(event.get("resource_type", "BILL")),
            resource_id=str(event["resource_id"]),
            parameter_summary=event.get("parameter_summary") or {},
            result="DENIED" if action.startswith("UNAUTHORIZED") else "SUCCESS",
            request_id=str(event.get("request_id", "")),
        )


class PlatformBillingConfirmationPort:
    """原子化消费确认令牌 + 审批（与 repair/announcement/inspection 同形）。

    ``create_draft``（咨询单写入）属于受控写：先消费审批（同一 UoW 内
    ``FOR UPDATE``，校验后置 ``CONSUMED``），再消费令牌作为纵深防御。
    """

    def __init__(self, session: Session, approval_service: ApprovalService) -> None:
        self._session = session
        self._approval_service = approval_service
        self._token_service = ConfirmationService(session)

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
                raise BillingError(
                    f"CONFIRMATION_{exc.code}",
                    exc.message,
                    exc.status_code,
                ) from exc
        if not token or not token.strip():
            raise BillingError(
                "CONFIRMATION_REQUIRED",
                "This operation requires a confirmation token.",
                422,
            )
        try:
            self._token_service.consume(
                token=token,
                actor_id=actor_id,
                action=action,
                parameter_hash=parameter_hash,
                request_id=request_id,
            )
        except InvalidConfirmationTokenException as exc:
            raise BillingError("CONFIRMATION_INVALID", exc.message, 422) from exc


def build_billing_ports(
    session: Session, approval_service: ApprovalService
) -> dict[str, Any]:
    """组装 billing UoW 需要的全部生产端口（approval_service 由容器装配后传入）。"""
    return {
        "idempotency": SqlAlchemyBillingIdempotencyPort(session),
        "audit": PlatformBillingAuditPort(session),
        "confirmations": PlatformBillingConfirmationPort(session, approval_service),
    }
