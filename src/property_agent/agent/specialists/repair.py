"""Stateless Repair pilot backed only by the Capability Executor."""

from dataclasses import replace
from typing import Any

from property_agent.agent.capabilities.contracts import (
    CapabilityInvocationState,
    CapabilityRuntimeContext,
    CapabilityWriteContext,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.state import GraphState


class RepairPilotSpecialist:
    """Translate deterministic Repair selections into typed capability calls."""

    def __init__(self, executor: CapabilityExecutor) -> None:
        self._executor = executor

    def invoke(self, state: GraphState, runtime: RuntimeContext) -> GraphState:
        name = str(state.slots.get("tool") or "repair_list")
        if name not in {"repair_list", "repair_get", "repair_create"}:
            raise ValueError(f"unsupported Repair pilot capability: {name}")
        invocation = replace(
            state.capability_invocation,
            selected_capability=name,
            human_confirmed=runtime.prepared_write is not None,
        )
        result = self._executor.execute(
            name,
            self._payload(name, state.slots),
            self._runtime(runtime, state),
            invocation,
        )
        state.capability_invocation = self._progress(invocation, result.fingerprint)
        if not result.ok:
            state.error = result.error.code if result.error else "CAPABILITY_EXECUTION_FAILED"
            state.tool_result = {
                "ok": False,
                "tool": result.capability,
                "error": {
                    "code": state.error,
                    "message": result.error.message if result.error else "操作失败。",
                },
            }
            state.add_message("assistant", result.error.message if result.error else "操作失败。")
            return state
        facts = result.output.model_dump(mode="json")
        state.tool_result = {"ok": True, "tool": result.capability, "data": facts}
        state.add_message("assistant", self._message(name, facts))
        return state

    @staticmethod
    def _runtime(runtime: RuntimeContext, state: GraphState) -> CapabilityRuntimeContext:
        prepared = runtime.prepared_write
        write = (
            CapabilityWriteContext(
                prepared.confirmation_token,
                prepared.idempotency_key,
                prepared.approval_ref,
            )
            if prepared is not None
            else None
        )
        return CapabilityRuntimeContext(
            request_context=runtime.request_context,
            current_house_id=runtime.current_house_id,
            legacy_state=state,
            write=write,
            trusted_runtime=runtime,
        )

    @staticmethod
    def _payload(name: str, slots: dict[str, Any]) -> dict[str, Any]:
        if name == "repair_get":
            return {"work_order_id": str(slots.get("work_order_id") or "")}
        if name == "repair_create":
            return {
                "description": str(slots.get("description") or ""),
                "location": str(slots.get("location") or ""),
                "urgency": str(slots.get("urgency") or "NORMAL"),
            }
        return {"statuses": tuple(slots.get("statuses") or ()), "limit": 20}

    @staticmethod
    def _progress(
        invocation: CapabilityInvocationState, fingerprint: str | None
    ) -> CapabilityInvocationState:
        prior = invocation.prior_fingerprints
        if fingerprint:
            prior = frozenset((*prior, fingerprint))
        return replace(
            invocation,
            step=invocation.step + 1,
            calls_made=invocation.calls_made + 1,
            fingerprint=fingerprint,
            prior_fingerprints=prior,
        )

    @staticmethod
    def _message(name: str, output: dict[str, Any]) -> str:
        if name == "repair_list":
            return f"共查到 {output.get('count', 0)} 条报修工单。"
        if name == "repair_get":
            return "已查到该报修工单的详情和进度。"
        return "报修工单已创建。"
