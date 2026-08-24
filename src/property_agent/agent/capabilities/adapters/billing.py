"""Typed billing adapters to existing Billing and Consultation services."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from pydantic import Field, model_validator

from property_agent.agent.capabilities.contracts import (
    CapabilityInput,
    CapabilityOutput,
    CapabilityRuntimeContext,
)

_FEE_TYPE_ALIASES = {
    "物业费": "PROPERTY",
    "物业管理费": "PROPERTY",
    "管理费": "PROPERTY",
    "水电费": "UTILITY",
    "水电": "UTILITY",
    "水费": "UTILITY",
    "电费": "UTILITY",
    "停车费": "PARKING",
    "车位费": "PARKING",
    "停车": "PARKING",
}


class BillBrief(CapabilityOutput):
    entity_type: str = "BILL"
    bill_id: str | None = None
    fee_type: str | None = None
    period: str | None = None
    total_amount: str | None = None
    amount: str | None = None
    property_fee: str
    utility_fee: str
    parking_fee: str
    late_fee: str
    status: str
    due_date: str | None = None


class BillingQueryInput(CapabilityInput):
    query_type: str = Field(default="list", pattern="^(list|detail|rule)$")
    period: str | None = Field(default=None, max_length=32)
    fee_type: str | None = Field(default=None, max_length=32)
    bill_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_query_shape(self) -> BillingQueryInput:
        if self.query_type == "detail" and not self.bill_id:
            raise ValueError("bill_id is required when query_type is detail")
        if self.query_type == "rule" and not self.fee_type:
            raise ValueError("fee_type is required when query_type is rule")
        return self


class BillingQueryOutput(CapabilityOutput):
    query_type: str
    period: str | None = None
    count: int | None = Field(default=None, ge=0)
    items: tuple[BillBrief, ...] = ()
    bill: BillBrief | None = None
    fee_type: str | None = None
    rule_known: bool | None = None


class BillingConsultInput(CapabilityInput):
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    bill_id: str | None = Field(default=None, max_length=64)


class ConsultationBrief(CapabilityOutput):
    id: str
    subject: str
    status: str
    bill_id: str | None = None


class BillingConsultOutput(CapabilityOutput):
    consultation: ConsultationBrief
    idempotency_key: str


def _normalize_fee_type(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    upper = text.upper()
    if upper in {"PROPERTY", "UTILITY", "PARKING"}:
        return upper
    return _FEE_TYPE_ALIASES.get(text)


def _bill_brief(bill: Any) -> BillBrief:
    period = getattr(bill, "bill_period", None) or getattr(bill, "period", None)
    total = getattr(bill, "total_amount", None)
    if total is None:
        total = getattr(bill, "amount", None)
    amount = str(total) if total is not None else None
    bill_id = getattr(bill, "bill_id", None) or getattr(bill, "id", None)
    return BillBrief(
        bill_id=str(bill_id) if bill_id is not None else None,
        fee_type=getattr(bill, "fee_type", None),
        period=period,
        total_amount=amount,
        amount=amount,
        property_fee=str(getattr(bill, "property_fee", "0.00")),
        utility_fee=str(getattr(bill, "utility_fee", "0.00")),
        parking_fee=str(getattr(bill, "parking_fee", "0.00")),
        late_fee=str(getattr(bill, "late_fee", "0.00")),
        status=str(getattr(bill, "status", "")),
        due_date=str(getattr(bill, "due_date", "") or "") or None,
    )


SessionProvider = Callable[[CapabilityRuntimeContext], Any]


class BillingQueryAdapter:
    def __init__(self, service: Any, session_provider: SessionProvider) -> None:
        self._service = service
        self._session_provider = session_provider

    def __call__(
        self, request: BillingQueryInput, runtime: CapabilityRuntimeContext
    ) -> BillingQueryOutput:
        db = self._session_provider(runtime)
        try:
            return self._query(request, runtime, db)
        finally:
            _close_session(db)

    def _query(self, request, runtime, db) -> BillingQueryOutput:
        if request.query_type == "detail":
            bill_id = cast(str, request.bill_id)
            bill, rule = self._service.get_bill(runtime.request_context, db, bill_id)
            return BillingQueryOutput(
                query_type="detail", bill=_bill_brief(bill), rule_known=rule is not None
            )
        if request.query_type == "rule":
            fee_type = cast(str, request.fee_type)
            rule = self._service.get_rule(runtime.request_context, db, fee_type)
            return BillingQueryOutput(
                query_type="rule", fee_type=fee_type, rule_known=rule is not None
            )
        bills = self._service.list_bills(
            runtime.request_context,
            db,
            fee_type=_normalize_fee_type(request.fee_type),
            period=request.period,
        )
        return BillingQueryOutput(
            query_type="list",
            period=request.period,
            count=len(bills),
            items=tuple(_bill_brief(bill) for bill in bills),
        )


class BillingConsultAdapter:
    def __init__(self, service: Any, session_provider: SessionProvider) -> None:
        self._service = service
        self._session_provider = session_provider

    def __call__(
        self, request: BillingConsultInput, runtime: CapabilityRuntimeContext
    ) -> BillingConsultOutput:
        if runtime.write is None:
            raise RuntimeError("billing_consult requires server write context")
        db = self._session_provider(runtime)
        try:
            ticket = self._service.create_draft(
                runtime.request_context,
                db,
                subject=request.subject,
                description=request.description,
                bill_id=request.bill_id,
                idempotency_key=runtime.write.idempotency_key,
                confirmation_token=runtime.write.confirmation_token,
                approval_ref=runtime.write.approval_ref,
            )
            return BillingConsultOutput(
                consultation=ConsultationBrief(
                    id=str(ticket.id),
                    subject=ticket.subject,
                    status=str(getattr(ticket, "status", "")),
                    bill_id=str(ticket.bill_id) if getattr(ticket, "bill_id", None) else None,
                ),
                idempotency_key=runtime.write.idempotency_key,
            )
        finally:
            _close_session(db)


def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        close()
