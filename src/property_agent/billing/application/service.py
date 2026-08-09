"""
application/service.py     账单与财务咨询应用服务（PRD 6.3）

- ``BillingService``：住户账单查询（社区 + 当前房屋范围过滤、费用类型/账期
  筛选、来源隔离、查询审计），以及规则查询（无有效规则声明未知）。
- ``ConsultationService``：财务咨询单全生命周期
  DRAFT→SUBMITTED→PROCESSING→ANSWERED→RESOLVED（APPEALED→PROCESSING）。

两服务都复用平台 RequestContext / AuditService / Idempotency，账单读路径经
``BillingSourcePort`` 隔离本地演示源与真实财务接口（R-02：源不可用时仍可保存
咨询草稿，绝不猜测金额）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from property_agent.billing.application.ports import IdempotencyRecord
from property_agent.billing.domain.entities import (
    ConsultationStatus,
    ConsultationTicket,
    ConsultationTransitionError,
)
from property_agent.billing.errors import BillingError, BillingSourceUnavailable, ConsultationError
from property_agent.billing.infrastructure.database import SessionLocal
from property_agent.billing.infrastructure.repositories import (
    SqlAlchemyBillingRuleRepository,
    SqlAlchemyBillRepository,
    SqlAlchemyConsultationRepository,
)
from property_agent.billing.infrastructure.shared_ports import (
    PlatformBillingAuditPort,
    SqlAlchemyBillingIdempotencyPort,
)
from property_agent.billing.infrastructure.source_port import LocalBillingSourcePort
from property_agent.platform.adapters.api.dependencies import RequestContext
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.infrastructure.orm_models import CommunityModel


def _community_code(ctx: RequestContext, platform_db: Session) -> str:
    """用平台 CommunityModel.name 作为账单社区码（轻量接入的社区标识）。"""
    community = platform_db.query(CommunityModel).filter_by(id=ctx.community_id).first()
    if community is None:
        raise BillingError("COMMUNITY_NOT_FOUND", "社区不存在", 404)
    return community.name


def _default_source_factory(bdb: Session):
    return LocalBillingSourcePort(SqlAlchemyBillRepository(bdb))


class BillingService:
    """住户账单查询 + 规则查询（PRD 6.3）。"""

    def __init__(self, source_port_factory: Callable[[Session], object] | None = None):
        self._source_factory = source_port_factory or _default_source_factory

    def list_bills(
        self,
        ctx: RequestContext,
        platform_db: Session,
        *,
        fee_type: str | None = None,
        period: str | None = None,
    ):
        """住户账单列表：社区 + 当前房屋范围过滤，查询审计。"""
        community_code = _community_code(ctx, platform_db)
        if ctx.current_house_id is None:
            raise BillingError(
                "HOUSE_SELECTION_REQUIRED",
                "请先选择当前房屋后再查询账单",
                400,
            )
        house_id = str(ctx.current_house_id)
        with SessionLocal() as bdb:
            source = self._source_factory(bdb)
            try:
                bills = source.list_bills(
                    community_id=community_code,
                    house_id=house_id,
                    fee_type=fee_type,
                    period=period,
                )
            except BillingSourceUnavailable:
                raise BillingError(
                    "BILLING_SOURCE_UNAVAILABLE", "账单服务暂时不可用", 503
                ) from None
        PlatformBillingAuditPort(platform_db).add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="BILL_QUERY",
            resource_type="BILL",
            resource_id=house_id,
            parameter_summary={"fee_type": fee_type, "period": period},
            request_id=ctx.request_id,
        )
        platform_db.commit()
        return bills

    def get_bill(self, ctx: RequestContext, platform_db: Session, bill_id: str):
        """账单详情：含适用规则（无有效规则时返回 unknown=True）。"""
        community_code = _community_code(ctx, platform_db)
        with SessionLocal() as bdb:
            source = self._source_factory(bdb)
            try:
                bill = source.get_bill(bill_id=bill_id)
            except BillingSourceUnavailable:
                raise BillingError(
                    "BILLING_SOURCE_UNAVAILABLE", "账单服务暂时不可用", 503
                ) from None
            if bill is None or bill.community_id != community_code:
                raise BillingError("BILL_NOT_FOUND", "账单不存在或无权访问", 404)
            rule = None
            if bill.fee_type:
                rule = SqlAlchemyBillingRuleRepository(bdb).find_effective(
                    community_code, bill.fee_type
                )
        PlatformBillingAuditPort(platform_db).add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="BILL_QUERY",
            resource_type="BILL",
            resource_id=bill_id,
            parameter_summary={},
            request_id=ctx.request_id,
        )
        platform_db.commit()
        return bill, rule

    def get_rule(
        self,
        ctx: RequestContext,
        platform_db: Session,
        fee_type: str,
    ):
        """查询当前生效规则；无有效规则返回 (None, unknown=True)。"""
        community_code = _community_code(ctx, platform_db)
        with SessionLocal() as bdb:
            rule = SqlAlchemyBillingRuleRepository(bdb).find_effective(community_code, fee_type)
        PlatformBillingAuditPort(platform_db).add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="BILL_RULE_QUERY",
            resource_type="BILL_RULE",
            resource_id=f"{community_code}:{fee_type}",
            parameter_summary={},
            request_id=ctx.request_id,
        )
        platform_db.commit()
        return rule


class ConsultationService:
    """财务咨询单全生命周期（PRD 6.3）。AI 只写文本答复，绝不改性账单。"""

    # ── 创建 ──────────────────────────────────────────

    def create_draft(
        self,
        ctx: RequestContext,
        platform_db: Session,
        *,
        subject: str,
        description: str,
        bill_id: str | None = None,
        idempotency_key: str,
    ) -> ConsultationTicket:
        """提交财务咨询草稿（幂等）。源不可用也不影响草稿保存（R-02）。"""
        community_code = _community_code(ctx, platform_db)
        request_hash = canonical_hash(
            {"subject": subject, "description": description, "bill_id": bill_id}
        )
        idem = SqlAlchemyBillingIdempotencyPort(platform_db)
        existing = idem.get(ctx.actor_id, "CREATE_CONSULTATION", idempotency_key)
        if existing is not None:
            return self.get(ctx, platform_db, str(existing.resource_id))

        ticket = ConsultationTicket(
            id=str(uuid4()),
            community_id=community_code,
            actor_id=str(ctx.actor_id),
            subject=subject,
            description=description,
            house_id=str(ctx.current_house_id) if ctx.current_house_id else None,
            bill_id=bill_id,
            status=ConsultationStatus.DRAFT,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with SessionLocal() as bdb:
            SqlAlchemyConsultationRepository(bdb).add(ticket)
            bdb.commit()

        idem.add(
            IdempotencyRecord(
                actor_id=ctx.actor_id,
                operation="CREATE_CONSULTATION",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=UUID(ticket.id),
                response_snapshot={"id": ticket.id, "status": ticket.status.value},
            )
        )
        PlatformBillingAuditPort(platform_db).add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="CONSULTATION_CREATE",
            resource_type="CONSULTATION",
            resource_id=ticket.id,
            parameter_summary={"subject": subject, "bill_id": bill_id},
            request_id=ctx.request_id,
        )
        platform_db.commit()
        return ticket

    # ── 状态推进 ──────────────────────────────────────

    def submit(
        self, ctx: RequestContext, platform_db: Session, consultation_id: str
    ) -> ConsultationTicket:
        return self._transition(
            ctx, platform_db, consultation_id, ConsultationStatus.SUBMITTED, owner_only=True
        )

    def start_processing(
        self, ctx: RequestContext, platform_db: Session, consultation_id: str
    ) -> ConsultationTicket:
        return self._transition(
            ctx, platform_db, consultation_id, ConsultationStatus.PROCESSING, staff_only=True
        )

    def answer(
        self, ctx: RequestContext, platform_db: Session, consultation_id: str, answer: str
    ) -> ConsultationTicket:
        ticket = self._transition(
            ctx, platform_db, consultation_id, ConsultationStatus.ANSWERED, staff_only=True
        )
        ticket.apply_answer(answer, handler_id=str(ctx.actor_id))
        with SessionLocal() as bdb:
            SqlAlchemyConsultationRepository(bdb).update(ticket)
            bdb.commit()
        PlatformBillingAuditPort(platform_db).add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="CONSULTATION_ANSWER",
            resource_type="CONSULTATION",
            resource_id=ticket.id,
            parameter_summary={"answered": True},
            request_id=ctx.request_id,
        )
        platform_db.commit()
        return ticket

    def resolve(
        self, ctx: RequestContext, platform_db: Session, consultation_id: str
    ) -> ConsultationTicket:
        return self._transition(
            ctx, platform_db, consultation_id, ConsultationStatus.RESOLVED, staff_only=True
        )

    def appeal(
        self, ctx: RequestContext, platform_db: Session, consultation_id: str
    ) -> ConsultationTicket:
        return self._transition(
            ctx, platform_db, consultation_id, ConsultationStatus.APPEALED, owner_only=True
        )

    def get(
        self, ctx: RequestContext, platform_db: Session, consultation_id: str
    ) -> ConsultationTicket:
        with SessionLocal() as bdb:
            ticket = SqlAlchemyConsultationRepository(bdb).get(consultation_id)
        if ticket is None:
            raise ConsultationError("CONSULTATION_NOT_FOUND", "咨询单不存在", 404)
        if ticket.community_id != _community_code(ctx, platform_db):
            raise ConsultationError("CONSULTATION_FORBIDDEN", "无权访问该咨询单", 403)
        return ticket

    def list_for_actor(self, ctx: RequestContext, platform_db: Session) -> list[ConsultationTicket]:
        community_code = _community_code(ctx, platform_db)
        with SessionLocal() as bdb:
            tickets = SqlAlchemyConsultationRepository(bdb).list_by_actor(
                str(ctx.actor_id), community_code
            )
        return tickets

    # ── 内部 ──────────────────────────────────────────

    def _transition(
        self,
        ctx: RequestContext,
        platform_db: Session,
        consultation_id: str,
        target: ConsultationStatus,
        *,
        owner_only: bool = False,
        staff_only: bool = False,
    ) -> ConsultationTicket:
        if staff_only and not ctx.has_any_role(
            "FINANCE", "MANAGER", "CUSTOMER_SERVICE", "SYSTEM_ADMIN"
        ):
            raise ConsultationError("CONSULTATION_FORBIDDEN", "仅财务人员/管理员可处理咨询单", 403)
        with SessionLocal() as bdb:
            repo = SqlAlchemyConsultationRepository(bdb)
            ticket = repo.get(consultation_id)
            if ticket is None:
                raise ConsultationError("CONSULTATION_NOT_FOUND", "咨询单不存在", 404)
            if ticket.community_id != _community_code(ctx, platform_db):
                raise ConsultationError("CONSULTATION_FORBIDDEN", "无权访问该咨询单", 403)
            if owner_only and ticket.actor_id != str(ctx.actor_id):
                raise ConsultationError("CONSULTATION_FORBIDDEN", "仅本人可操作该咨询单", 403)
            try:
                ticket.transition_to(target)
            except ConsultationTransitionError as exc:
                raise ConsultationError("CONSULTATION_ILLEGAL_STATE", str(exc), 409) from exc
            repo.update(ticket)
            bdb.commit()
        PlatformBillingAuditPort(platform_db).add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action=f"CONSULTATION_{target.value}",
            resource_type="CONSULTATION",
            resource_id=ticket.id,
            parameter_summary={},
            request_id=ctx.request_id,
        )
        platform_db.commit()
        return ticket
