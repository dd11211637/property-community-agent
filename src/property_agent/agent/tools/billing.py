"""账单工具 — 只调用 ``BillingService`` / ``ConsultationService``（PRD §6.3）。

- ``billing_query``：只读（账单列表 / 详情 / 计费规则）
- ``billing_consult``：写-低风险，创建财务咨询草稿；AI 只写文本诉求，
  **绝不改动金额或账单状态**。

账单源不可用时（R-02）不编造数字，原样回传不可用状态与错误码。
"""

from collections.abc import Callable
from typing import Any

from property_agent.agent.policies import OperationLevel
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import (
    ContextProvider,
    Tool,
    assert_level,
    idempotency_key,
    ok,
    require_confirmation,
    require_slot,
)
from property_agent.billing.errors import BillingError


def _bill_brief(bill: Any) -> dict[str, Any]:
    return {
        "bill_id": getattr(bill, "bill_id", None) or getattr(bill, "id", None),
        "fee_type": getattr(bill, "fee_type", None),
        "period": getattr(bill, "period", None),
        "amount": str(getattr(bill, "amount", "")) or None,
        "status": str(getattr(bill, "status", "")),
        "due_date": str(getattr(bill, "due_date", "") or "") or None,
    }


def _failure(tool: str, exc: BillingError) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "error_code": getattr(exc, "code", "BILLING_ERROR"),
        "reason": str(exc),
    }


def build_billing_tools(
    billing_service: Any,
    consultation_service: Any,
    context_provider: ContextProvider,
    session_provider: Callable[[GraphState], Any],
) -> dict[str, Tool]:
    def billing_query(state: GraphState) -> dict[str, Any]:
        assert_level("billing_query", OperationLevel.READ)
        ctx = context_provider(state)
        db = session_provider(state)
        query_type = str(state.slots.get("query_type") or "list").lower()
        try:
            if query_type == "detail":
                bill_id = str(require_slot(state, "bill_id", "billing_query"))
                bill, rule = billing_service.get_bill(ctx, db, bill_id)
                return ok(
                    "billing_query",
                    query_type="detail",
                    bill=_bill_brief(bill),
                    rule_known=rule is not None,
                )
            if query_type == "rule":
                fee_type = str(require_slot(state, "fee_type", "billing_query"))
                rule = billing_service.get_rule(ctx, db, fee_type)
                return ok(
                    "billing_query",
                    query_type="rule",
                    fee_type=fee_type,
                    rule_known=rule is not None,
                )
            bills = billing_service.list_bills(
                ctx,
                db,
                fee_type=state.slots.get("fee_type"),
                period=state.slots.get("period"),
            )
            return ok(
                "billing_query",
                query_type="list",
                count=len(bills),
                items=[_bill_brief(b) for b in bills],
            )
        except BillingError as exc:
            return _failure("billing_query", exc)

    def billing_consult(state: GraphState) -> dict[str, Any]:
        assert_level("billing_consult", OperationLevel.WRITE_LOW_RISK)
        require_confirmation(state, "billing_consult")
        ctx = context_provider(state)
        db = session_provider(state)
        subject = str(require_slot(state, "subject", "billing_consult"))
        description = str(require_slot(state, "description", "billing_consult"))
        bill_id = state.slots.get("bill_id")
        key = idempotency_key(
            state,
            "billing_consult",
            {"subject": subject, "description": description, "bill_id": bill_id},
        )
        try:
            ticket = consultation_service.create_draft(
                ctx,
                db,
                subject=subject,
                description=description,
                bill_id=str(bill_id) if bill_id else None,
                idempotency_key=key,
            )
        except BillingError as exc:
            return _failure("billing_consult", exc)
        return ok(
            "billing_consult",
            consultation={
                "id": ticket.id,
                "subject": ticket.subject,
                "status": str(getattr(ticket, "status", "")),
                "bill_id": getattr(ticket, "bill_id", None),
            },
            idempotency_key=key,
        )

    return {"billing_query": billing_query, "billing_consult": billing_consult}
