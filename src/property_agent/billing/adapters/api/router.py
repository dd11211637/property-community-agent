"""
adapters/api/router.py     账单与财务咨询安全路由（PRD 6.3）

所有端点均经平台鉴权接缝 ``get_request_context``（生产环境由 JWT 绑定），并要求
住户已选择当前房屋（``current_house_id``）方可查询账单；所有账单查询均落审计。

- ``GET  /api/bills``            当前房屋账单列表（社区 + 当前房屋范围过滤）
- ``GET  /api/bills/{bill_id}``  账单详情 + 适用规则（无规则声明 unknown）
- ``GET  /api/bills/rules/{fee_type}``  当前生效规则（无规则声明 unknown）
- ``POST /api/consultations``     创建财务咨询草稿（幂等）
- ``GET  /api/consultations``     我的咨询单列表
- ``GET  /api/consultations/{id}``  咨询单详情
- ``POST /api/consultations/{id}/submit``     提交（仅本人）
- ``POST /api/consultations/{id}/process``    开始处理（仅财务/管理员）
- ``POST /api/consultations/{id}/answer``     答复（仅财务/管理员，仅文本）
- ``POST /api/consultations/{id}/resolve``    解决（仅财务/管理员）
- ``POST /api/consultations/{id}/appeal``     申诉（仅本人）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from property_agent.billing.adapters.api.dependencies import (
    get_billing_service,
    get_consultation_service,
)
from property_agent.billing.adapters.api.schemas import (
    BillDetailResponse,
    BillingRuleLookupResponse,
    BillingRuleResponse,
    BillResponse,
    ConsultationResponse,
)
from property_agent.billing.application.service import (
    BillingService,
    ConsultationService,
)
from property_agent.billing.domain.entities import Bill, ConsultationTicket
from property_agent.platform.adapters.api.dependencies import get_current_house_context
from property_agent.platform.context import RequestContext
from property_agent.platform.dependencies import get_request_context
from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.responses import success_envelope
from property_agent.platform.schemas import Envelope

router = APIRouter(prefix="/api/billing", tags=["billing"])

BillingServiceDep = Annotated[BillingService, Depends(get_billing_service)]
ConsultationServiceDep = Annotated[ConsultationService, Depends(get_consultation_service)]
ContextDep = Annotated[RequestContext, Depends(get_request_context)]
HouseContextDep = Annotated[RequestContext, Depends(get_current_house_context)]
DbDep = Annotated[object, Depends(get_db)]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


# ── 请求体 ─────────────────────────────────────────────


class CreateConsultationRequest(BaseModel):
    subject: str
    description: str
    bill_id: str | None = None
    confirmation_token: str = Field(min_length=1)


class AnswerConsultationRequest(BaseModel):
    answer: str
    expected_version: int = Field(ge=1)


class VersionedConsultationRequest(BaseModel):
    expected_version: int = Field(ge=1)


# ── 表现层 ─────────────────────────────────────────────


def bill_data(bill: Bill) -> BillResponse:
    return BillResponse.model_validate(
        {
            "bill_id": bill.bill_id,
            "bill_period": bill.bill_period,
            "property_fee": bill.property_fee,
            "utility_fee": bill.utility_fee,
            "parking_fee": bill.parking_fee,
            "late_fee": bill.late_fee,
            "total_amount": bill.total_amount,
            "due_date": bill.due_date,
            "status": bill.status.value if hasattr(bill.status, "value") else bill.status,
            "payment_time": bill.payment_time,
            "receipt_no": bill.receipt_no,
            "community_id": bill.community_id,
            "house_id": bill.house_id,
            "version": bill.version,
            "fee_type": bill.fee_type,
            "source_time": bill.source_time,
            "rule_version": bill.rule_version,
            "rule_name": bill.rule_name,
            "user_name": bill.user_name,
            "building_name": bill.building_name,
            "room_number": bill.room_number,
        }
    )


def rule_data(rule) -> BillingRuleResponse | None:
    if rule is None:
        return None
    return BillingRuleResponse.model_validate(
        {
            "id": rule.id,
            "community_id": rule.community_id,
            "fee_type": rule.fee_type,
            "version": rule.version,
            "name": rule.name,
            "parameters": rule.parameters,
            "valid_from": rule.valid_from,
            "valid_until": rule.valid_until,
        }
    )


def consultation_data(ticket: ConsultationTicket) -> ConsultationResponse:
    return ConsultationResponse.model_validate(
        {
            "id": ticket.id,
            "community_id": ticket.community_id,
            "actor_id": ticket.actor_id,
            "subject": ticket.subject,
            "description": ticket.description,
            "house_id": ticket.house_id,
            "bill_id": ticket.bill_id,
            "status": ticket.status.value,
            "answer": ticket.answer,
            "handler_id": ticket.handler_id,
            "version": ticket.version,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
        }
    )


# ── 账单查询 ───────────────────────────────────────────


@router.get("/bills", response_model=Envelope[list[BillResponse]])
def list_bills(
    service: BillingServiceDep,
    context: HouseContextDep,
    db: DbDep,
    fee_type: Annotated[str | None, Query()] = None,
    period: Annotated[str | None, Query()] = None,
) -> Envelope:
    bills = service.list_bills(context, db, fee_type=fee_type, period=period)
    return success_envelope([bill_data(b) for b in bills], context)


@router.get("/bills/{bill_id}", response_model=Envelope[BillDetailResponse])
def get_bill(
    bill_id: str, service: BillingServiceDep, context: HouseContextDep, db: DbDep
) -> Envelope:
    bill, rule = service.get_bill(context, db, bill_id)
    return success_envelope(
        {
            "bill": bill_data(bill),
            "rule": rule_data(rule),
            "unknown_rule": rule is None,
            "consultation_entry": "POST /api/billing/consultations" if rule is None else None,
        },
        context,
    )


@router.get("/bills/rules/{fee_type}", response_model=Envelope[BillingRuleLookupResponse])
def get_rule(
    fee_type: str, service: BillingServiceDep, context: HouseContextDep, db: DbDep
) -> Envelope:
    rule = service.get_rule(context, db, fee_type)
    return success_envelope(
        {
            "fee_type": fee_type,
            "rule": rule_data(rule),
            "unknown_rule": rule is None,
            "consultation_entry": "POST /api/billing/consultations" if rule is None else None,
        },
        context,
    )


# ── 财务咨询单 ─────────────────────────────────────────


@router.post("/consultations", response_model=Envelope[ConsultationResponse], status_code=201)
def create_consultation(
    payload: CreateConsultationRequest,
    idempotency_key: IdempotencyHeader,
    service: ConsultationServiceDep,
    context: HouseContextDep,
    db: DbDep,
) -> Envelope:
    ticket = service.create_draft(
        context,
        db,
        subject=payload.subject,
        description=payload.description,
        bill_id=payload.bill_id,
        idempotency_key=idempotency_key,
        confirmation_token=payload.confirmation_token,
    )
    return success_envelope(consultation_data(ticket), context)


@router.get("/consultations", response_model=Envelope[list[ConsultationResponse]])
def list_my_consultations(
    service: ConsultationServiceDep, context: ContextDep, db: DbDep
) -> Envelope:
    tickets = service.list_for_actor(context, db)
    return success_envelope([consultation_data(t) for t in tickets], context)


@router.get("/consultations/{consultation_id}", response_model=Envelope[ConsultationResponse])
def get_consultation(
    consultation_id: str, service: ConsultationServiceDep, context: ContextDep, db: DbDep
) -> Envelope:
    ticket = service.get(context, db, consultation_id)
    return success_envelope(consultation_data(ticket), context)


@router.post(
    "/consultations/{consultation_id}/submit", response_model=Envelope[ConsultationResponse]
)
def submit_consultation(
    consultation_id: str,
    payload: VersionedConsultationRequest,
    service: ConsultationServiceDep,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    ticket = service.submit(context, db, consultation_id, expected_version=payload.expected_version)
    return success_envelope(consultation_data(ticket), context)


@router.post(
    "/consultations/{consultation_id}/process", response_model=Envelope[ConsultationResponse]
)
def process_consultation(
    consultation_id: str,
    payload: VersionedConsultationRequest,
    service: ConsultationServiceDep,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    ticket = service.start_processing(
        context, db, consultation_id, expected_version=payload.expected_version
    )
    return success_envelope(consultation_data(ticket), context)


@router.post(
    "/consultations/{consultation_id}/answer", response_model=Envelope[ConsultationResponse]
)
def answer_consultation(
    consultation_id: str,
    payload: AnswerConsultationRequest,
    service: ConsultationServiceDep,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    ticket = service.answer(
        context,
        db,
        consultation_id,
        payload.answer,
        expected_version=payload.expected_version,
    )
    return success_envelope(consultation_data(ticket), context)


@router.post(
    "/consultations/{consultation_id}/resolve", response_model=Envelope[ConsultationResponse]
)
def resolve_consultation(
    consultation_id: str,
    payload: VersionedConsultationRequest,
    service: ConsultationServiceDep,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    ticket = service.resolve(
        context, db, consultation_id, expected_version=payload.expected_version
    )
    return success_envelope(consultation_data(ticket), context)


@router.post(
    "/consultations/{consultation_id}/appeal", response_model=Envelope[ConsultationResponse]
)
def appeal_consultation(
    consultation_id: str,
    payload: VersionedConsultationRequest,
    service: ConsultationServiceDep,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    ticket = service.appeal(context, db, consultation_id, expected_version=payload.expected_version)
    return success_envelope(consultation_data(ticket), context)
