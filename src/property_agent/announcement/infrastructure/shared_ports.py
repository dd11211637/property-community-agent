"""
Production implementations of the announcement module's shared ports — PRD 6.2.

The announcement module shipped with Protocol declarations plus test fakes, so
the assembled application answered ``503 ADAPTER_NOT_CONFIGURED``. This module
provides the real, database-backed adapters:

==================  ==========================================================
Port                Backing implementation
==================  ==========================================================
IdempotencyPort     ``idempotency_records`` table (two-phase get / add)
ConfirmationPort    ``platform.ConfirmationService`` (publish二次确认)
AudienceResolver    ``user_house_bindings`` + ``houses`` + ``users``
AuditPort           ``platform.AuditService`` (auto masking)
MessagePort         ``platform.MessageOutboxService`` (outbox, deduplicated)
==================  ==========================================================

Every adapter shares the *same* SQLAlchemy ``Session`` as the announcement
repository, so one ``uow.commit()`` atomically persists the announcement, its
version history, the frozen audience snapshot, the audit trail and every
station message. Platform exceptions are translated into announcement
``BusinessError`` values so the API keeps emitting the unified envelope.

Audience resolution fails **closed**: only the three whitelisted structured
dimensions (``building_ids`` / ``unit_ids`` / ``house_types``) are accepted,
both the house and the user must belong to the current community, and only
ACTIVE users with an ACTIVE binding to an ACTIVE house are counted. There is no
path from a client string to raw SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.agent.infrastructure.run_lease import (
    StaleAgentRunError,
    assert_run_fence,
)
from property_agent.announcement.application.ports import (
    AudienceResolverPort,
    AuditPort,
    ConfirmationPort,
    IdempotencyPort,
    IdempotencyRecord,
    MessagePort,
)
from property_agent.announcement.domain.entities import AudienceSnapshot
from property_agent.announcement.domain.policies import AUDIENCE_FIELDS
from property_agent.platform.application.approval_service import ApprovalError, ApprovalService
from property_agent.platform.application.audit_service import AuditService
from property_agent.platform.application.confirmation_service import ConfirmationService
from property_agent.platform.application.platform_confirmation_port import (
    _current_agent_lease,
)
from property_agent.platform.domain.exceptions import InvalidConfirmationTokenException
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import (
    HouseModel,
    IdempotencyRecordModel,
    UserHouseBindingModel,
    UserModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import MessageOutboxService

#: Number of recipient examples returned with an audience preview.
AUDIENCE_SAMPLE_SIZE = 5

#: Human-readable templates for the station-message outbox.
_MESSAGE_TEMPLATES: dict[str, tuple[str, str]] = {
    "ANNOUNCEMENT_PUBLISHED": ("小区公告", "您有一条新的小区公告，请查看详情。"),
    "ANNOUNCEMENT_WITHDRAWN": ("公告已撤回", "此前发布的一条公告已被撤回。"),
}


def _template(event_type: str) -> tuple[str, str]:
    return _MESSAGE_TEMPLATES.get(event_type, ("小区公告", f"公告事件：{event_type}。"))


def _mask_name(value: str | None) -> str:
    """Keep only the first character of a recipient name in previews."""
    if not value:
        return "***"
    return f"{value[0]}**"


# ═══════════════════════════════════════════════════════════════
# IdempotencyPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyIdempotencyPort:
    """Two-phase idempotency on the shared ``idempotency_records`` table.

    ``get`` is side-effect free — the service compares the stored request hash
    itself and decides between *replay* and ``IDEMPOTENCY_CONFLICT``. ``add``
    writes the record together with the announcement snapshot inside the same
    transaction as the business write, so "the first call timed out but had in
    fact succeeded → the retry returns the same announcement" holds (PRD 12.3).
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
            # Registered but never completed — let the caller retry instead of
            # replaying a half-written response.
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
    """原子化消费确认令牌 + 审批（P0 正确性底座，与 repair 端口一致语义）。

    发布是高风险写操作（PRD 6.2/11）。``approval_ref`` 由服务端在确认时
    创建的 PENDING 审批引用，在业务 UoW 内与 mutation / 审计 / Outbox 同
    事务消费（CONSUMED）；令牌仍按既有规则消费作为纵深防御。
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
        lease = _current_agent_lease()
        if self._enforce_fence and lease is None:
            raise StaleAgentRunError(
                "<production-fence>",
                reason="fencing enforced but no active lease present in production",
            )
        if lease is not None:
            assert_run_fence(self._session, lease)
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
                "A confirmation token is required for publishing.",
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
# AudienceResolverPort
# ═══════════════════════════════════════════════════════════════


class SqlAlchemyAudienceResolverPort:
    """Resolve a structured audience condition into concrete recipients.

    Only whitelisted dimensions are honoured and each one is applied as an
    ``IN`` filter over an indexed column — an unknown key raises instead of
    being ignored, because silently dropping a filter would *broaden* the
    audience of an announcement.

    An empty condition means "every resident of the community", which is the
    documented default for community-wide notices.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self, *, community_id: UUID, condition: dict[str, list[str]], request_id: str
    ) -> AudienceSnapshot:
        unsupported = sorted(set(condition) - AUDIENCE_FIELDS)
        if unsupported:
            raise BusinessError(
                "VALIDATION_ERROR",
                "Audience condition contains unsupported fields.",
                422,
                {"unsupported_fields": unsupported},
            )

        statement = (
            select(
                UserModel.id,
                UserModel.display_name,
                HouseModel.building,
                HouseModel.unit,
                HouseModel.room_no,
            )
            .join(UserHouseBindingModel, UserHouseBindingModel.user_id == UserModel.id)
            .join(HouseModel, HouseModel.id == UserHouseBindingModel.house_id)
            .where(
                # Tenancy isolation on both sides — a house of this community
                # can never pull in a user of another community.
                UserModel.community_id == community_id,
                HouseModel.community_id == community_id,
                UserModel.status == "ACTIVE",
                UserHouseBindingModel.status == "ACTIVE",
                HouseModel.status == "ACTIVE",
            )
            .order_by(HouseModel.building, HouseModel.unit, HouseModel.room_no)
        )

        if condition.get("building_ids"):
            statement = statement.where(HouseModel.building.in_(condition["building_ids"]))
        if condition.get("unit_ids"):
            statement = statement.where(HouseModel.unit.in_(condition["unit_ids"]))
        if condition.get("house_types"):
            statement = statement.where(HouseModel.house_type.in_(condition["house_types"]))

        member_ids: list[UUID] = []
        seen: set[UUID] = set()
        samples: list[dict[str, str]] = []
        for user_id, display_name, building, unit, room_no in self._session.execute(statement):
            if user_id in seen:
                # One resident may be bound to several matching houses.
                continue
            seen.add(user_id)
            member_ids.append(user_id)
            if len(samples) < AUDIENCE_SAMPLE_SIZE:
                samples.append(
                    {
                        "receiver": _mask_name(display_name),
                        "address": f"{building}-{unit}-{room_no}",
                    }
                )

        return AudienceSnapshot(
            condition=dict(condition),
            member_ids=tuple(member_ids),
            count=len(member_ids),
            samples=tuple(samples),
            generated_at=datetime.now(UTC),
        )


# ═══════════════════════════════════════════════════════════════
# AuditPort
# ═══════════════════════════════════════════════════════════════


class PlatformAuditPort:
    """Write audit rows through the platform service (sensitive data masked)."""

    def __init__(self, session: Session) -> None:
        self._service = AuditService(session)

    def add(self, **event: Any) -> None:
        action = str(event["action"])
        self._service.log(
            actor_id=event["actor_id"],
            community_id=event["community_id"],
            action=action if action.startswith("ANNOUNCEMENT") else f"ANNOUNCEMENT_{action}",
            resource_type=str(event.get("resource_type", "ANNOUNCEMENT")),
            resource_id=str(event["resource_id"]),
            parameter_summary=event.get("parameter_summary") or {},
            result="DENIED" if action.startswith("UNAUTHORIZED") else "SUCCESS",
            request_id=str(event.get("request_id", "")),
        )


# ═══════════════════════════════════════════════════════════════
# MessagePort
# ═══════════════════════════════════════════════════════════════


class PlatformMessagePort:
    """Enqueue station messages into the transactional outbox.

    PRD 6.2 keeps delivery status independent from announcement status: the
    outbox row is written in the publish transaction, and a later delivery
    failure never rolls the announcement back out of ``PUBLISHED``.

    The idempotency key is derived from (resource, event, receiver) so that a
    retried publish — which replays the same announcement — never produces
    duplicate notifications for the same resident.
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
            business_type="ANNOUNCEMENT",
            resource_id=str(resource_id),
            title=title,
            body=body,
            idempotency_key=f"ANNOUNCEMENT:{resource_id}:{event_type}:{receiver_id}",
        )


# ═══════════════════════════════════════════════════════════════
# Assembly
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class AnnouncementSharedPorts:
    """The shared ports an announcement Unit of Work exposes."""

    idempotency: IdempotencyPort
    confirmations: ConfirmationPort
    audiences: AudienceResolverPort
    audit: AuditPort
    messages: MessagePort


def build_announcement_ports(
    session: Session, approval_service: ApprovalService, *, enforce_fence: bool = False
) -> AnnouncementSharedPorts:
    """Create every production shared port bound to one SQLAlchemy session."""
    return AnnouncementSharedPorts(
        idempotency=SqlAlchemyIdempotencyPort(session),
        confirmations=PlatformConfirmationPort(
            session, approval_service, error_factory=BusinessError, enforce_fence=enforce_fence
        ),
        audiences=SqlAlchemyAudienceResolverPort(session),
        audit=PlatformAuditPort(session),
        messages=PlatformMessagePort(session),
    )
