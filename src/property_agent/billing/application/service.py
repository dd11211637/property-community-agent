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

from property_agent.billing.application.ports import BillingUnitOfWorkPort, IdempotencyRecord
from property_agent.billing.domain.entities import (
    ConsultationStatus,
    ConsultationTicket,
    ConsultationTransitionError,
)
from property_agent.billing.errors import BillingError, BillingSourceUnavailable, ConsultationError
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.context import RequestContext

BillingUnitOfWorkFactory = Callable[[object], BillingUnitOfWorkPort]


class BillingService:
    """住户账单查询 + 规则查询（PRD 6.3）。"""

    def __init__(self, uow_factory: BillingUnitOfWorkFactory):
        self._uow_factory = uow_factory

    def list_bills(
        self,
        ctx: RequestContext,
        transaction: object,
        *,
        fee_type: str | None = None,
        period: str | None = None,
    ):
        """住户账单列表：社区 + 当前房屋范围过滤，查询审计。"""
        uow = self._uow_factory(transaction)
        community_code = uow.community_code(ctx.community_id)
        if ctx.current_house_id is None:
            raise BillingError(
                "HOUSE_SELECTION_REQUIRED",
                "请先选择当前房屋后再查询账单",
                400,
            )
        house_id = str(ctx.current_house_id)
        source = uow.source
        try:
            bills = source.list_bills(
                community_id=community_code,
                house_id=house_id,
                fee_type=fee_type,
                period=period,
            )
        except BillingSourceUnavailable:
            raise BillingError("BILLING_SOURCE_UNAVAILABLE", "账单服务暂时不可用", 503) from None
        uow.audit.add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="BILL_QUERY",
            resource_type="BILL",
            resource_id=house_id,
            parameter_summary={"fee_type": fee_type, "period": period},
            request_id=ctx.request_id,
        )
        uow.commit()
        return bills

    def get_bill(self, ctx: RequestContext, transaction: object, bill_id: str):
        """账单详情：含适用规则（无有效规则时返回 unknown=True）。"""
        uow = self._uow_factory(transaction)
        community_code = uow.community_code(ctx.community_id)
        if ctx.current_house_id is None:
            raise BillingError("HOUSE_SELECTION_REQUIRED", "请先选择当前房屋后再查询账单", 400)
        source = uow.source
        try:
            bill = source.get_bill(bill_id=bill_id)
        except BillingSourceUnavailable:
            raise BillingError("BILLING_SOURCE_UNAVAILABLE", "账单服务暂时不可用", 503) from None
        if (
            bill is None
            or bill.community_id != community_code
            or bill.house_id != str(ctx.current_house_id)
        ):
            raise BillingError("BILL_NOT_FOUND", "账单不存在或无权访问", 404)
        rule = None
        if bill.fee_type:
            rule = uow.rules.find_effective(community_code, bill.fee_type)
        uow.audit.add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="BILL_QUERY",
            resource_type="BILL",
            resource_id=bill_id,
            parameter_summary={},
            request_id=ctx.request_id,
        )
        uow.commit()
        return bill, rule

    def get_rule(
        self,
        ctx: RequestContext,
        transaction: object,
        fee_type: str,
    ):
        """查询当前生效规则；无有效规则返回 (None, unknown=True)。"""
        uow = self._uow_factory(transaction)
        community_code = uow.community_code(ctx.community_id)
        rule = uow.rules.find_effective(community_code, fee_type)
        uow.audit.add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="BILL_RULE_QUERY",
            resource_type="BILL_RULE",
            resource_id=f"{community_code}:{fee_type}",
            parameter_summary={},
            request_id=ctx.request_id,
        )
        uow.commit()
        return rule


# ── 财务咨询 P0 原子化助手 ──────────────────────────────────────


def require_consultation_confirmation(confirmation_token: str | None) -> None:
    """咨询草稿是受控写：必须带服务端签发的确认令牌才能继续。"""
    if not confirmation_token or not confirmation_token.strip():
        raise ConsultationError(
            "CONFIRMATION_REQUIRED",
            "创建财务咨询草稿需要确认令牌。",
            422,
        )


def consume_consultation_approval(
    uow: BillingUnitOfWorkPort,
    *,
    ctx: RequestContext,
    approval_ref: str | None,
    confirmation_token: str,
    subject: str,
    description: str,
    bill_id: str | None,
) -> None:
    """P0：在 caller 的 UoW session 内消费审批 + 令牌（同一事务）。"""
    param_hash = canonical_hash(
        {"subject": subject, "description": description, "bill_id": bill_id}
    )
    uow.confirmations.consume(
        approval_ref=approval_ref,
        token=confirmation_token,
        actor_id=ctx.actor_id,
        action="CREATE_CONSULTATION",
        parameter_hash=param_hash,
        request_id=ctx.request_id,
    )


def validate_consultation_bill(
    uow: BillingUnitOfWorkPort,
    ctx: RequestContext,
    *,
    bill_id: str | None,
    community_code: str,
) -> None:
    """若带 bill_id，验证账单属于当前社区与当前房屋（R-02）。"""
    if bill_id is None:
        return
    bill = uow.source.get_bill(bill_id=bill_id)
    if (
        ctx.current_house_id is None
        or bill is None
        or bill.community_id != community_code
        or bill.house_id != str(ctx.current_house_id)
    ):
        raise BillingError("BILL_NOT_FOUND", "关联账单不存在或无权访问", 404)


def _maybe_idempotent_replay(
    service: ConsultationService,
    uow: BillingUnitOfWorkPort,
    *,
    ctx: RequestContext,
    transaction: object,
    idempotency_key: str,
    request_hash: str,
) -> ConsultationTicket | None:
    """幂等检查：同一键命中且参数一致则返回原 ticket，否则抛冲突。"""
    existing = uow.idempotency.get(ctx.actor_id, "CREATE_CONSULTATION", idempotency_key)
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise ConsultationError("IDEMPOTENCY_CONFLICT", "该幂等键已用于不同的咨询内容", 409)
    snapshot_id = (existing.response_snapshot or {}).get("id")
    consultation_id = snapshot_id or existing.resource_id.hex
    return service.get(ctx, transaction, consultation_id)


class ConsultationService:
    """财务咨询单全生命周期（PRD 6.3）。AI 只写文本答复，绝不改性账单。"""

    def __init__(self, uow_factory: BillingUnitOfWorkFactory):
        self._uow_factory = uow_factory

    # ── 创建 ──────────────────────────────────────────

    def create_draft(
        self,
        ctx: RequestContext,
        transaction: object,
        *,
        subject: str,
        description: str,
        bill_id: str | None = None,
        idempotency_key: str,
        confirmation_token: str | None = None,
        approval_ref: str | None = None,
    ) -> ConsultationTicket:
        """提交财务咨询草稿（幂等）。源不可用也不影响草稿保存（R-02）。

        P0 原子化：消费审批 + 令牌与本方法后续 mutation / 审计 / 幂等同事务提交。
        """
        require_consultation_confirmation(confirmation_token)
        uow = self._uow_factory(transaction)
        request_hash = canonical_hash(
            {"subject": subject, "description": description, "bill_id": bill_id}
        )
        replay = _maybe_idempotent_replay(
            self,
            uow,
            ctx=ctx,
            transaction=transaction,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay

        consume_consultation_approval(
            uow,
            ctx=ctx,
            approval_ref=approval_ref,
            confirmation_token=confirmation_token,
            subject=subject,
            description=description,
            bill_id=bill_id,
        )
        community_code = uow.community_code(ctx.community_id)
        validate_consultation_bill(
            uow, ctx, bill_id=bill_id, community_code=community_code
        )

        ticket = ConsultationTicket(
            id=uuid4().hex,
            community_id=community_code,
            actor_id=str(ctx.actor_id),
            subject=subject,
            description=description,
            house_id=str(ctx.current_house_id) if ctx.current_house_id else None,
            bill_id=bill_id,
            status=ConsultationStatus.DRAFT,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        uow.consultations.add(ticket)

        uow.idempotency.add(
            IdempotencyRecord(
                actor_id=ctx.actor_id,
                operation="CREATE_CONSULTATION",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=UUID(ticket.id),
                response_snapshot={"id": ticket.id, "status": ticket.status.value},
            )
        )
        uow.audit.add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action="CONSULTATION_CREATE",
            resource_type="CONSULTATION",
            resource_id=ticket.id,
            parameter_summary={"subject": subject, "bill_id": bill_id},
            request_id=ctx.request_id,
        )
        uow.commit()
        return ticket

    # ── 状态推进 ──────────────────────────────────────

    def submit(
        self,
        ctx: RequestContext,
        transaction: object,
        consultation_id: str,
        *,
        expected_version: int,
    ) -> ConsultationTicket:
        return self._transition(
            ctx,
            transaction,
            consultation_id,
            ConsultationStatus.SUBMITTED,
            expected_version=expected_version,
            owner_only=True,
        )

    def start_processing(
        self,
        ctx: RequestContext,
        transaction: object,
        consultation_id: str,
        *,
        expected_version: int,
    ) -> ConsultationTicket:
        return self._transition(
            ctx,
            transaction,
            consultation_id,
            ConsultationStatus.PROCESSING,
            expected_version=expected_version,
            staff_only=True,
        )

    def answer(
        self,
        ctx: RequestContext,
        transaction: object,
        consultation_id: str,
        answer: str,
        *,
        expected_version: int,
    ) -> ConsultationTicket:
        return self._transition(
            ctx,
            transaction,
            consultation_id,
            ConsultationStatus.ANSWERED,
            expected_version=expected_version,
            staff_only=True,
            answer=answer,
        )

    def resolve(
        self,
        ctx: RequestContext,
        transaction: object,
        consultation_id: str,
        *,
        expected_version: int,
    ) -> ConsultationTicket:
        return self._transition(
            ctx,
            transaction,
            consultation_id,
            ConsultationStatus.RESOLVED,
            expected_version=expected_version,
            staff_only=True,
        )

    def appeal(
        self,
        ctx: RequestContext,
        transaction: object,
        consultation_id: str,
        *,
        expected_version: int,
    ) -> ConsultationTicket:
        return self._transition(
            ctx,
            transaction,
            consultation_id,
            ConsultationStatus.APPEALED,
            expected_version=expected_version,
            owner_only=True,
        )

    def get(
        self, ctx: RequestContext, transaction: object, consultation_id: str
    ) -> ConsultationTicket:
        uow = self._uow_factory(transaction)
        ticket = uow.consultations.get(consultation_id)
        if ticket is None:
            raise ConsultationError("CONSULTATION_NOT_FOUND", "咨询单不存在", 404)
        if ticket.community_id != uow.community_code(ctx.community_id):
            raise ConsultationError("CONSULTATION_FORBIDDEN", "无权访问该咨询单", 403)
        if not ctx.has_any_role(
            "FINANCE", "FINANCE_STAFF", "MANAGER", "CUSTOMER_SERVICE", "SYSTEM_ADMIN"
        ) and ticket.actor_id != str(ctx.actor_id):
            raise ConsultationError("CONSULTATION_FORBIDDEN", "无权访问该咨询单", 403)
        return ticket

    def list_for_actor(self, ctx: RequestContext, transaction: object) -> list[ConsultationTicket]:
        uow = self._uow_factory(transaction)
        community_code = uow.community_code(ctx.community_id)
        tickets = uow.consultations.list_by_actor(str(ctx.actor_id), community_code)
        return tickets

    # ── 内部 ──────────────────────────────────────────

    def _transition(
        self,
        ctx: RequestContext,
        transaction: object,
        consultation_id: str,
        target: ConsultationStatus,
        *,
        expected_version: int,
        owner_only: bool = False,
        staff_only: bool = False,
        answer: str | None = None,
    ) -> ConsultationTicket:
        if staff_only and not ctx.has_any_role(
            "FINANCE", "FINANCE_STAFF", "MANAGER", "CUSTOMER_SERVICE", "SYSTEM_ADMIN"
        ):
            raise ConsultationError("CONSULTATION_FORBIDDEN", "仅财务人员/管理员可处理咨询单", 403)
        uow = self._uow_factory(transaction)
        repo = uow.consultations
        ticket = repo.get(consultation_id)
        if ticket is None:
            raise ConsultationError("CONSULTATION_NOT_FOUND", "咨询单不存在", 404)
        if ticket.community_id != uow.community_code(ctx.community_id):
            raise ConsultationError("CONSULTATION_FORBIDDEN", "无权访问该咨询单", 403)
        if owner_only and ticket.actor_id != str(ctx.actor_id):
            raise ConsultationError("CONSULTATION_FORBIDDEN", "仅本人可操作该咨询单", 403)
        if ticket.version != expected_version:
            raise ConsultationError(
                "VERSION_CONFLICT",
                "咨询单已被其他操作更新，请刷新后重试",
                409,
                {"current_version": ticket.version},
            )
        try:
            ticket.transition_to(target)
        except ConsultationTransitionError as exc:
            raise ConsultationError("CONSULTATION_ILLEGAL_STATE", str(exc), 409) from exc
        if answer is not None:
            ticket.apply_answer(answer, handler_id=str(ctx.actor_id))
        repo.update(ticket, expected_version=expected_version)
        uow.audit.add(
            actor_id=ctx.actor_id,
            community_id=ctx.community_id,
            action=f"CONSULTATION_{target.value}",
            resource_type="CONSULTATION",
            resource_id=ticket.id,
            parameter_summary={"answered": answer is not None},
            request_id=ctx.request_id,
        )
        uow.commit()
        return ticket
