"""报修工具 — 只调用 ``WorkOrderService`` 公开方法（PRD §6.1 / §6.5.2）。

- ``repair_list`` / ``repair_get``：只读
- ``repair_create``：写-低风险，必须先确认；高风险报修由 Service 拒绝下单并
  转人工工单，工具把它翻译成接管指令而不是伪装成成功。
"""

from dataclasses import replace
from functools import partial
from typing import Any

from property_agent.agent.capabilities.adapters.repair import (
    RepairCreateAdapter,
    RepairGetAdapter,
    RepairListAdapter,
    normalize_repair_urgency,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import (
    CapabilityResult,
    CapabilityRuntimeContext,
    CapabilityWriteContext,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import CapabilityPolicy
from property_agent.agent.policies import OperationLevel
from property_agent.agent.runtime import ExecutionPolicy, RuntimeContext
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import (
    ContextProvider,
    Tool,
    ToolPreconditionError,
    assert_level,
    handover,
    idempotency_key,
    ok,
    require_confirmation,
    require_slot,
)
from property_agent.agent.tools.capability_bridge import LegacyCapabilityError
from property_agent.repair.domain.classification import classify_repair_category
from property_agent.repair.domain.enums import RepairCategory


def normalize_repair_category(value: Any) -> RepairCategory:
    """Compatibility helper retained for callers outside the migrated path."""
    if isinstance(value, RepairCategory):
        return value
    text = str(value).strip()
    try:
        return RepairCategory(text.upper())
    except ValueError:
        return classify_repair_category(text)


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
    context = context_provider(state)
    trusted_runtime = RuntimeContext.from_request_context(
        context,
        conversation_id=state.conversation_id,
        current_house_id=state.current_house_id,
        execution_policy=ExecutionPolicy(allowlist=frozenset({name})),
    )
    invocation = replace(
        state.capability_invocation,
        prior_fingerprints=frozenset(),
        fingerprint=None,
        selected_capability=name,
        human_confirmed=confirmed,
    )
    result = executor.execute(
        name,
        payload,
        CapabilityRuntimeContext(
            context,
            state.current_house_id,
            legacy_state=state,
            write=write,
            trusted_runtime=trusted_runtime,
        ),
        invocation,
    )
    if result.fingerprint is not None:
        state.capability_invocation = replace(
            invocation,
            step=invocation.step + 1,
            calls_made=invocation.calls_made + 1,
            prior_fingerprints=state.capability_invocation.prior_fingerprints
            | {result.fingerprint},
            fingerprint=result.fingerprint,
        )
    return result


def build_repair_tools(
    service: Any,
    context_provider: ContextProvider,
    capability_executor: CapabilityExecutor | None = None,
) -> dict[str, Tool]:
    executor = capability_executor or CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {
            "repair_list": RepairListAdapter(service),
            "repair_get": RepairGetAdapter(service),
            "repair_create": RepairCreateAdapter(service),
        },
    )

    invoke = partial(_invoke_capability, executor, context_provider)

    def repair_list(state: GraphState) -> dict[str, Any]:
        assert_level("repair_list", OperationLevel.READ)
        result = invoke(
            state,
            "repair_list",
            {
                "statuses": tuple(state.slots.get("statuses") or ()),
                "limit": int(state.slots.get("limit") or 20),
            },
        )
        return _tool_result("repair_list", result)

    def repair_get(state: GraphState) -> dict[str, Any]:
        assert_level("repair_get", OperationLevel.READ)
        result = invoke(
            state,
            "repair_get",
            {"work_order_id": str(require_slot(state, "work_order_id", "repair_get"))},
        )
        return _tool_result("repair_get", result)

    def repair_create(state: GraphState) -> dict[str, Any]:
        assert_level("repair_create", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "repair_create")
        if state.current_house_id is None:
            raise ToolPreconditionError("repair_create 需要先选择当前房屋")

        description = str(require_slot(state, "description", "repair_create")).strip()
        # Model/free-form category values are non-authoritative. Persist only
        # the deterministic classification of the resident's symptom.
        category = classify_repair_category(description)
        state.slots["category"] = category.value
        location = str(require_slot(state, "location", "repair_create"))
        urgency = normalize_repair_urgency(state.slots.get("urgency")).value
        key = idempotency_key(
            state,
            "repair_create",
            {
                "house_id": state.current_house_id,
                "category": category,
                "location": location,
                "description": description,
                "urgency": urgency,
            },
        )
        result = invoke(
            state,
            "repair_create",
            {
                "description": description,
                "location": location,
                "urgency": urgency,
            },
            confirmed=True,
            write=CapabilityWriteContext(
                confirmation_token=token,
                approval_ref=state.approval_ref,
                idempotency_key=key,
            ),
        )
        if result.error and result.error.code == "HANDOVER_REQUIRED":
            return handover("repair_create", result.error.message, **result.error.details)
        data = _output_or_raise(result)
        # 回写工单号到会话槽位：后续"改口/回归"轮次据此识别已有活跃工单，
        # 避免对同一报修反复建单。
        state.slots["work_order_id"] = str(data["work_order"]["business_no"])
        return ok("repair_create", **data)

    return {
        "repair_list": repair_list,
        "repair_get": repair_get,
        "repair_create": repair_create,
    }


def _output_or_raise(result) -> dict[str, Any]:
    if result.error is not None:
        raise LegacyCapabilityError(
            result.error.code,
            result.error.message,
            dict(result.error.details),
        )
    assert result.output is not None
    return result.output.model_dump(mode="json")


def _tool_result(tool: str, result) -> dict[str, Any]:
    if result.error is not None and result.error.kind == "business":
        return {
            "ok": False,
            "tool": tool,
            "error_code": result.error.code,
            "reason": result.error.message,
        }
    return ok(tool, **_output_or_raise(result))
