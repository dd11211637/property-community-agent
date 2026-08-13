"""Message-center operations and community-scoped management aggregates."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from property_agent.inspection.infrastructure.models import SecurityEventModel
from property_agent.platform.application.audit_service import AuditService
from property_agent.platform.application.idempotency_service import IdempotencyService
from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import (
    HandoverTicketModel,
    MessageRecordModel,
    UserModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import MAX_RETRY_COUNT


def _message_dict(
    message: MessageRecordModel,
    *,
    fallback_contact: str | None,
    handover_status: str | None,
) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "business_type": message.business_type,
        "resource_id": message.resource_id,
        "title": message.title,
        "body": message.body,
        "status": message.status,
        "is_read": message.read_at is not None,
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "retry_count": message.retry_count,
        "max_retry_count": MAX_RETRY_COUNT,
        "retry_exhausted": message.retry_count >= MAX_RETRY_COUNT,
        "last_error": message.last_error,
        "handover_status": handover_status,
        "fallback_contact": fallback_contact,
        "created_at": message.created_at.isoformat(),
        "updated_at": message.updated_at.isoformat(),
    }


class MessageCenterService:
    """Current-user message queries and idempotent read operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_messages(
        self,
        context: RequestContext,
        *,
        status: str | None,
        business_type: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        query = self._session.query(MessageRecordModel).filter(
            MessageRecordModel.receiver_id == context.actor_id
        )
        if status == "READ":
            query = query.filter(MessageRecordModel.read_at.is_not(None))
        elif status == "UNREAD":
            query = query.filter(MessageRecordModel.read_at.is_(None))
        elif status:
            query = query.filter(MessageRecordModel.status == status)
        if business_type:
            query = query.filter(MessageRecordModel.business_type == business_type)

        total = query.count()
        messages = (
            query.order_by(MessageRecordModel.created_at.desc()).offset(offset).limit(limit).all()
        )
        return {
            "items": self._serialize_messages(messages, context.actor_id),
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def mark_read(
        self,
        context: RequestContext,
        *,
        message_id: UUID,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation = "MESSAGE_MARK_READ"
        request_body = {"message_id": str(message_id)}
        idempotency = IdempotencyService(self._session)
        cached = idempotency.check(
            actor_id=context.actor_id,
            operation=operation,
            key=idempotency_key,
            request_body=request_body,
        )
        if cached is not None:
            return cached

        message = (
            self._session.query(MessageRecordModel)
            .filter(
                MessageRecordModel.id == message_id,
                MessageRecordModel.receiver_id == context.actor_id,
            )
            .first()
        )
        if message is None:
            self._session.rollback()
            raise BusinessError("MESSAGE_NOT_FOUND", "消息不存在。", 404)

        already_read = message.read_at is not None
        if not already_read:
            message.read_at = datetime.now(timezone.utc)
        result = self._serialize_messages([message], context.actor_id)[0]
        AuditService(self._session).log(
            actor_id=context.actor_id,
            community_id=context.community_id,
            action="MESSAGE_READ",
            resource_type="MESSAGE",
            resource_id=str(message.id),
            parameter_summary={"already_read": already_read},
            result="SUCCESS",
            request_id=context.request_id,
        )
        idempotency.update_snapshot(
            actor_id=context.actor_id,
            operation=operation,
            key=idempotency_key,
            resource_id=str(message.id),
            response_snapshot=result,
        )
        self._session.commit()
        return result

    def mark_all_read(self, context: RequestContext, *, idempotency_key: str) -> dict[str, Any]:
        operation = "MESSAGE_MARK_ALL_READ"
        request_body: dict[str, Any] = {}
        idempotency = IdempotencyService(self._session)
        cached = idempotency.check(
            actor_id=context.actor_id,
            operation=operation,
            key=idempotency_key,
            request_body=request_body,
        )
        if cached is not None:
            return cached

        now = datetime.now(timezone.utc)
        updated_count = (
            self._session.query(MessageRecordModel)
            .filter(
                MessageRecordModel.receiver_id == context.actor_id,
                MessageRecordModel.read_at.is_(None),
            )
            .update({MessageRecordModel.read_at: now}, synchronize_session=False)
        )
        result = {"updated_count": updated_count, "read_at": now.isoformat()}
        AuditService(self._session).log(
            actor_id=context.actor_id,
            community_id=context.community_id,
            action="MESSAGE_READ_ALL",
            resource_type="MESSAGE",
            parameter_summary={"updated_count": updated_count},
            result="SUCCESS",
            request_id=context.request_id,
        )
        idempotency.update_snapshot(
            actor_id=context.actor_id,
            operation=operation,
            key=idempotency_key,
            resource_id=str(context.actor_id),
            response_snapshot=result,
        )
        self._session.commit()
        return result

    def _serialize_messages(
        self, messages: list[MessageRecordModel], receiver_id: UUID
    ) -> list[dict[str, Any]]:
        user = self._session.get(UserModel, receiver_id)
        fallback_contact = (user.phone or user.email) if user else None
        message_ids = [str(message.id) for message in messages]
        handovers = (
            self._session.query(HandoverTicketModel)
            .filter(
                HandoverTicketModel.resource_type == "MESSAGE",
                HandoverTicketModel.resource_id.in_(message_ids),
            )
            .all()
            if message_ids
            else []
        )
        handover_by_resource = {ticket.resource_id: ticket.status for ticket in handovers}
        return [
            _message_dict(
                message,
                fallback_contact=fallback_contact if message.status == "FAILED" else None,
                handover_status=handover_by_resource.get(
                    str(message.id), "NOT_CREATED" if message.status == "FAILED" else None
                ),
            )
            for message in messages
        ]


class AdminDashboardService:
    """Read-only community aggregates for management roles."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_dashboard(self, context: RequestContext) -> dict[str, Any]:
        database_status = self._database_status()
        pending_statuses = ("PENDING", "ASSIGNED", "PROCESSING")
        pending_query = self._session.query(HandoverTicketModel).filter(
            HandoverTicketModel.community_id == context.community_id,
            HandoverTicketModel.status.in_(pending_statuses),
        )
        pending_items = (
            pending_query.order_by(HandoverTicketModel.created_at.desc()).limit(20).all()
        )

        failed_query = (
            self._session.query(MessageRecordModel)
            .join(UserModel, UserModel.id == MessageRecordModel.receiver_id)
            .filter(
                UserModel.community_id == context.community_id,
                MessageRecordModel.status == "FAILED",
            )
        )
        failed_messages = (
            failed_query.order_by(MessageRecordModel.updated_at.desc()).limit(20).all()
        )
        failed_count = failed_query.count()

        risk_query = self._session.query(SecurityEventModel).filter(
            SecurityEventModel.community_id == context.community_id,
            SecurityEventModel.risk_level.in_(("HIGH_RISK", "HIGH")),
            SecurityEventModel.status != "CLOSED",
        )
        risk_events = risk_query.order_by(SecurityEventModel.updated_at.desc()).limit(20).all()

        users = {
            user.id: user
            for user in self._session.query(UserModel)
            .filter(UserModel.id.in_([message.receiver_id for message in failed_messages]))
            .all()
        }
        return {
            "pending_count": pending_query.count(),
            "failed_message_count": failed_count,
            "high_risk_event_count": risk_query.count(),
            "pending_items": [
                {
                    "id": str(ticket.id),
                    "source": ticket.source,
                    "queue": ticket.queue,
                    "summary": ticket.summary,
                    "status": ticket.status,
                    "created_at": ticket.created_at.isoformat(),
                }
                for ticket in pending_items
            ],
            "failed_messages": [
                _message_dict(
                    message,
                    fallback_contact=(
                        users[message.receiver_id].phone or users[message.receiver_id].email
                    )
                    if message.receiver_id in users
                    else None,
                    handover_status=self._handover_status(message.id),
                )
                for message in failed_messages
            ],
            "high_risk_events": [
                {
                    "id": str(event.id),
                    "business_no": event.business_no,
                    "location": event.location,
                    "risk_level": event.risk_level,
                    "status": event.status,
                    "updated_at": event.updated_at.isoformat(),
                }
                for event in risk_events
            ],
            "integration_health": {
                "database": database_status,
                "message_delivery": "DEGRADED" if failed_count else "UP",
                # 配置存在不等于外部模型已通过在线探测，避免把配置状态
                # 误报成健康状态。真实调用失败仍由 Agent 降级和指标暴露。
                "model_gateway": "CONFIGURED_NOT_PROBED"
                if os.getenv("DEEPSEEK_API_KEY")
                else "DETERMINISTIC_FALLBACK",
            },
        }

    def _database_status(self) -> str:
        try:
            self._session.execute(text("SELECT 1"))
        except Exception:
            return "DOWN"
        return "UP"

    def _handover_status(self, message_id: UUID) -> str:
        status = (
            self._session.query(HandoverTicketModel.status)
            .filter_by(resource_type="MESSAGE", resource_id=str(message_id))
            .scalar()
        )
        return status or "NOT_CREATED"
