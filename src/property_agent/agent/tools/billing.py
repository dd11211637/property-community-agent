"""账单工具 — 只调用 ``BillingService`` / ``ConsultationService``（PRD §6.3）。

- ``billing_query``：只读（账单列表 / 详情 / 计费规则）
- ``billing_consult``：写-低风险，创建财务咨询草稿；AI 只写文本诉求，
  **绝不改动金额或账单状态**。

账单源不可用时（R-02）不编造数字，原样回传不可用状态与错误码。
"""

from collections.abc import Callable
from functools import partial
from typing import Any

from property_agent.agent.capabilities.adapters.billing import (
    BillingConsultAdapter,
    BillingQueryAdapter,
)
from property_agent.agent.capabilities.adapters.billing import (
    _normalize_fee_type as _capability_normalize_fee_type,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import (
    CapabilityInvocationState,
    CapabilityResult,
    CapabilityRuntimeContext,
    CapabilityWriteContext,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import CapabilityPolicy
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


def _normalize_fee_type(value: Any) -> str | None:
    """Compatibility facade; canonical normalization lives in the typed adapter."""
    return _capability_normalize_fee_type(None if value is None else str(value))


def _invoke_capability(
    executor: CapabilityExecutor,
    context_provider: ContextProvider,
    state: GraphState,
    name: str,
    payload: dict[str, Any],
    *,
    confirmed: bool = False,
    write: CapabilityWriteContext | None = None,
) -> CapabilityResult:
    return executor.execute(
        name,
        payload,
        CapabilityRuntimeContext(
            context_provider(state), state.current_house_id, legacy_state=state, write=write
        ),
        CapabilityInvocationState(allowlist=frozenset({name}), human_confirmed=confirmed),
    )


def build_billing_tools(
    billing_service: Any,
    consultation_service: Any,
    context_provider: ContextProvider,
    session_provider: Callable[[GraphState], Any],
    capability_executor: CapabilityExecutor | None = None,
) -> dict[str, Tool]:
    executor = capability_executor or CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {
            "billing_query": BillingQueryAdapter(
                billing_service, lambda runtime: session_provider(runtime.legacy_state)
            ),
            "billing_consult": BillingConsultAdapter(
                consultation_service, lambda runtime: session_provider(runtime.legacy_state)
            ),
        },
    )

    invoke = partial(_invoke_capability, executor, context_provider)

    def billing_query(state: GraphState) -> dict[str, Any]:
        assert_level("billing_query", OperationLevel.READ)
        query_type = str(state.slots.get("query_type") or "list").lower()
        bill_id = state.slots.get("bill_id")
        fee_type = state.slots.get("fee_type")
        if query_type == "detail":
            bill_id = require_slot(state, "bill_id", "billing_query")
        elif query_type == "rule":
            fee_type = require_slot(state, "fee_type", "billing_query")
        result = invoke(
            state,
            "billing_query",
            {
                "query_type": query_type,
                "period": state.slots.get("period"),
                "fee_type": str(fee_type) if fee_type is not None else None,
                "bill_id": str(bill_id) if bill_id is not None else None,
            },
        )
        return _tool_result("billing_query", result)

    def billing_consult(state: GraphState) -> dict[str, Any]:
        assert_level("billing_consult", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "billing_consult")
        subject = str(require_slot(state, "subject", "billing_consult"))
        description = str(require_slot(state, "description", "billing_consult"))
        bill_id = state.slots.get("bill_id")
        key = idempotency_key(
            state,
            "billing_consult",
            {"subject": subject, "description": description, "bill_id": bill_id},
        )
        result = invoke(
            state,
            "billing_consult",
            {
                "subject": subject,
                "description": description,
                "bill_id": str(bill_id) if bill_id else None,
            },
            confirmed=True,
            write=CapabilityWriteContext(
                confirmation_token=token,
                approval_ref=state.approval_ref,
                idempotency_key=key,
            ),
        )
        return _tool_result("billing_consult", result)

    return {"billing_query": billing_query, "billing_consult": billing_consult}


def _tool_result(tool: str, result) -> dict[str, Any]:
    if result.error is not None:
        return {
            "ok": False,
            "tool": tool,
            "error_code": result.error.code,
            "reason": result.error.message,
        }
    assert result.output is not None
    data = result.output.model_dump(mode="json")
    if tool == "billing_query":
        query_type = data["query_type"]
        keys = {
            "list": ("query_type", "period", "count", "items"),
            "detail": ("query_type", "bill", "rule_known"),
            "rule": ("query_type", "fee_type", "rule_known"),
        }[query_type]
        data = {key: data[key] for key in keys}
    return ok(tool, **data)
