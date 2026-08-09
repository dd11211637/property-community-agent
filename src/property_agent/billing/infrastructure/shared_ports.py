"""
infrastructure/shared_ports.py     账单模块生产端口（PRD 6.3）

提供幂等端口（复用平台 idempotency_records 表）与审计端口（复用平台
AuditService）。与 announcement/repair 一致：所有适配器共享同一 SQLAlchemy
Session，平台异常翻译为 billing BusinessError。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.billing.application.ports import IdempotencyRecord
from property_agent.platform.application.audit_service import AuditService
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
