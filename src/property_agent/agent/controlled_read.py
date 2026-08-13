"""Bounded ReAct-style runtime for authenticated read-only business queries."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any
from uuid import uuid4

from property_agent.agent.policies import Intent, OperationLevel
from property_agent.agent.read_contracts import (
    AgentTrace,
    FactsPackage,
    Observation,
    PlannerAction,
    PlannerDecision,
    ReadRunStatus,
    ReadToolSpec,
)
from property_agent.agent.state import GraphState

ReadTool = Callable[[GraphState, dict[str, Any]], dict[str, Any]]
_FORBIDDEN_ARGUMENTS = frozenset(
    {
        "actor_id",
        "community_id",
        "house_id",
        "current_house_id",
        "roles",
        "confirmation_token",
        "idempotency_key",
        "expected_version",
        "database_url",
    }
)


def is_controlled_read(state: GraphState) -> bool:
    if state.intent not in {
        Intent.REPAIR.value,
        Intent.ANNOUNCEMENT.value,
        Intent.BILLING.value,
        Intent.GENERAL_HELP.value,
        Intent.INSPECTION.value,
    }:
        return False
    action = str(state.slots.get("action") or "").lower()
    if state.intent == Intent.ANNOUNCEMENT.value:
        # Only established query verbs may enter the read-only ReAct loop.
        # Drafting and every review/write synonym must stay in the deterministic
        # announcement subgraph, even when a model emits a new synonym.
        return action in {"", "list", "get", "detail", "query", "search"}
    write_actions = {"create", "publish", "release", "send", "consult", "submit"}
    if action in write_actions:
        return False
    if state.intent == Intent.REPAIR.value:
        user_text = str(state.slots.get("user_text") or "")
        query_markers = (
            "查询工单",
            "工单进度",
            "报修进度",
            "维修进度",
            "查工单",
            "报修记录",
        )
        return (
            action in {"query", "list", "get", "detail"}
            or bool(state.slots.get("work_order_id"))
            or any(marker in user_text for marker in query_markers)
        )
    if state.intent == Intent.BILLING.value:
        return action not in {"consult", "create"}
    if state.intent == Intent.GENERAL_HELP.value:
        user_text = str(state.slots.get("user_text") or "")
        knowledge_markers = (
            "物业电话",
            "联系方式",
            "停车",
            "装修",
            "门禁",
            "垃圾",
            "开放时间",
            "社区规定",
            "物业规定",
        )
        return any(marker in user_text for marker in knowledge_markers)
    if state.intent == Intent.INSPECTION.value:
        return action in {"", "query", "list", "get", "detail", "query_task", "query_event"}
    return action not in {"publish", "release", "send", "create"}


class ReadPlanGuard:
    def __init__(self, specs: Mapping[str, ReadToolSpec], *, max_steps: int) -> None:
        self._specs = specs
        self._max_steps = max_steps

    def validate(
        self,
        decision: PlannerDecision,
        *,
        step: int,
        fingerprints: set[str],
    ) -> tuple[ReadToolSpec, str] | None:
        if decision.action != PlannerAction.CALL_TOOL:
            return None
        if step >= self._max_steps:
            raise ValueError("MAX_STEPS_EXCEEDED")
        spec = self._specs.get(str(decision.tool))
        if spec is None:
            raise ValueError("UNKNOWN_READ_TOOL")
        keys = set(decision.arguments)
        if keys & _FORBIDDEN_ARGUMENTS:
            raise ValueError("TRUSTED_ARGUMENT_OVERRIDE")
        if keys - spec.allowed_arguments:
            raise ValueError("UNSUPPORTED_TOOL_ARGUMENT")
        if spec.required_arguments - keys:
            raise ValueError("MISSING_TOOL_ARGUMENT")
        self._validate_argument_values(decision.arguments)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"tool": spec.name, "arguments": decision.arguments},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint in fingerprints:
            raise ValueError("REPEATED_TOOL_CALL")
        return spec, fingerprint

    @staticmethod
    def _validate_argument_values(arguments: dict[str, Any]) -> None:
        for key, value in arguments.items():
            if key in {"assigned_to_me"}:
                if not isinstance(value, bool):
                    raise ValueError("INVALID_TOOL_ARGUMENT")
                continue
            if key == "limit" and (
                isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20
            ):
                raise ValueError("INVALID_TOOL_ARGUMENT")
            if key in {"statuses", "risk_levels"} and (
                not isinstance(value, list | tuple)
                or len(value) > 10
                or not all(isinstance(item, str) and len(item) <= 32 for item in value)
            ):
                raise ValueError("INVALID_TOOL_ARGUMENT")
            if key not in {"limit", "statuses", "risk_levels"} and (
                not isinstance(value, str) or not value.strip() or len(value) > 128
            ):
                raise ValueError("INVALID_TOOL_ARGUMENT")
        period = arguments.get("period")
        if period and not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period):
            raise ValueError("INVALID_TOOL_ARGUMENT")
        target_date = arguments.get("target_date")
        if target_date and not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", target_date):
            raise ValueError("INVALID_TOOL_ARGUMENT")
        topic = arguments.get("topic")
        if topic and topic not in {"WATER_OUTAGE", "POWER_OUTAGE"}:
            raise ValueError("INVALID_TOOL_ARGUMENT")


def build_controlled_read_node(
    *,
    planner: Any,
    specs: Mapping[str, ReadToolSpec],
    tools: Mapping[str, ReadTool],
    max_steps: int = 5,
    max_duration_seconds: float = 20.0,
):
    guard = ReadPlanGuard(specs, max_steps=max_steps)

    def node(state: GraphState) -> GraphState:
        question = str(state.slots.get("user_text") or "")
        observations: list[Observation] = []
        fingerprints: set[str] = set()
        trace = AgentTrace(trace_id=f"react_{uuid4().hex}")
        finish_reason = "ANSWER_READY"
        deadline = time.monotonic() + max_duration_seconds

        for step in range(max_steps + 1):
            if time.monotonic() >= deadline:
                finish_reason = "RUN_TIMEOUT"
                trace.status = ReadRunStatus.REJECTED
                trace.add("guardrail_rejected", step=step, code=finish_reason)
                break
            try:
                decision = planner.plan_read(
                    question=question,
                    intent=str(state.intent or ""),
                    slots=deepcopy(state.slots),
                    trusted_context=deepcopy(state.trusted_context),
                    observations=[item.to_dict() for item in observations],
                    tools=[spec.public_schema() for spec in specs.values()],
                )
                if not isinstance(decision, PlannerDecision):
                    decision = PlannerDecision.from_dict(decision)
            except Exception:
                trace.degraded = True
                trace.add("planner_degraded", step=step)
                try:
                    decision = planner.deterministic_plan_read(
                        question=question,
                        intent=str(state.intent or ""),
                        slots=deepcopy(state.slots),
                        trusted_context=deepcopy(state.trusted_context),
                        observations=[item.to_dict() for item in observations],
                        tools=[spec.public_schema() for spec in specs.values()],
                    )
                except Exception:
                    finish_reason = "PLANNER_UNAVAILABLE"
                    trace.status = ReadRunStatus.REJECTED
                    trace.add("planner_failed", step=step, code=finish_reason)
                    break

            trace.add(
                "planner_decision",
                step=step,
                action=decision.action.value,
                tool=decision.tool,
                reason_code=decision.reason_code,
            )
            if decision.action != PlannerAction.CALL_TOOL:
                finish_reason = decision.reason_code or decision.action.value
                break
            try:
                validated = guard.validate(decision, step=step, fingerprints=fingerprints)
            except ValueError as exc:
                finish_reason = str(exc)
                trace.add("guardrail_rejected", step=step, code=finish_reason)
                if finish_reason == "REPEATED_TOOL_CALL" and observations:
                    try:
                        decision = planner.deterministic_plan_read(
                            question=question,
                            intent=str(state.intent or ""),
                            slots=deepcopy(state.slots),
                            trusted_context=deepcopy(state.trusted_context),
                            observations=[item.to_dict() for item in observations],
                            tools=[spec.public_schema() for spec in specs.values()],
                        )
                    except Exception:
                        finish_reason = "PLANNER_UNAVAILABLE"
                        trace.status = ReadRunStatus.REJECTED
                        trace.add("planner_failed", step=step, code=finish_reason)
                        break
                    trace.degraded = True
                    trace.add(
                        "planner_degraded",
                        step=step,
                        reason="REPEATED_TOOL_CALL_BLOCKED",
                    )
                    trace.add(
                        "planner_decision",
                        step=step,
                        action=decision.action.value,
                        tool=decision.tool,
                        reason_code=decision.reason_code,
                        source="deterministic_fallback",
                    )
                    if decision.action != PlannerAction.CALL_TOOL:
                        finish_reason = decision.reason_code or "ANSWER_READY"
                        break
                    try:
                        validated = guard.validate(decision, step=step, fingerprints=fingerprints)
                    except ValueError as fallback_exc:
                        finish_reason = str(fallback_exc)
                        trace.status = ReadRunStatus.REJECTED
                        trace.add(
                            "guardrail_rejected",
                            step=step,
                            code=finish_reason,
                            source="deterministic_fallback",
                        )
                        break
                else:
                    trace.status = ReadRunStatus.REJECTED
                    break
            assert validated is not None
            spec, fingerprint = validated
            fingerprints.add(fingerprint)
            trace.add(
                "tool_call",
                step=step,
                tool=spec.name,
                arguments_hash=fingerprint,
                guardrail="PASSED",
            )
            try:
                result = tools[spec.name](state, decision.arguments)
                ok = result.get("ok") is True
                data = result.get("data") if isinstance(result.get("data"), dict) else {}
                if isinstance(data.get("items"), list):
                    data = {**data, "items": data["items"][: spec.max_result_records]}
                data = _bounded_observation_data(data)
                _assert_output_scope(data, state)
                observation = Observation(
                    tool=spec.name,
                    ok=ok,
                    data=data,
                    error_code=None if ok else str(result.get("error_code") or "TOOL_FAILED"),
                    error_message=(None if ok else str(result.get("reason") or "")[:256] or None),
                    step=step,
                )
            except Exception:
                observation = Observation(
                    tool=spec.name,
                    ok=False,
                    error_code="TOOL_EXECUTION_FAILED",
                    step=step,
                )
            observations.append(observation)
            trace.add(
                "observation",
                step=step,
                tool=spec.name,
                ok=observation.ok,
                record_count=len(observation.data.get("items") or []),
                error_code=observation.error_code,
            )
        else:  # pragma: no cover - defensive; loop normally exits via guard
            finish_reason = "MAX_STEPS_EXCEEDED"

        if trace.status != ReadRunStatus.REJECTED:
            trace.status = ReadRunStatus.DEGRADED if trace.degraded else ReadRunStatus.COMPLETED
        trace.finish_reason = finish_reason
        facts = FactsPackage(question, tuple(observations), trace).to_dict()
        state.read_facts = facts
        state.read_trace = trace.to_dict()
        state.tool_result = _presentation_result(observations, facts)
        state.operation_level = OperationLevel.READ.value
        return state

    return node


def _bounded_observation_data(value: Any, *, depth: int = 0) -> Any:
    """Bound model-visible facts without changing their business meaning."""
    if depth >= 6:
        return None
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, dict):
        return {
            str(key)[:64]: _bounded_observation_data(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, list | tuple):
        return [_bounded_observation_data(item, depth=depth + 1) for item in value[:20]]
    return value


def _assert_output_scope(data: Any, state: GraphState) -> None:
    """Reject any adapter output that explicitly contradicts authenticated scope."""
    trusted_community = str(state.community_id or "")
    trusted_house = str(state.current_house_id or "")
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "community_id" and value and str(value) != trusted_community:
                raise ValueError("OUTPUT_SCOPE_MISMATCH")
            if key in {"house_id", "current_house_id"} and value and str(value) != trusted_house:
                raise ValueError("OUTPUT_SCOPE_MISMATCH")
            _assert_output_scope(value, state)
    elif isinstance(data, list | tuple):
        for value in data:
            _assert_output_scope(value, state)


def _presentation_result(observations: list[Observation], facts: dict[str, Any]) -> dict[str, Any]:
    business = [
        item
        for item in observations
        if item.tool
        in {
            "search_announcements",
            "search_community_knowledge",
            "list_bills",
            "get_bill",
            "list_work_orders",
            "get_work_order",
            "list_inspection_tasks",
            "get_inspection_task",
            "list_security_events",
            "get_security_event",
        }
    ]
    selected = business[-1] if business else (observations[-1] if observations else None)
    if selected is None:
        return {
            "ok": False,
            "tool": "controlled_read",
            "error_code": "NO_OBSERVATION",
            "reason": "未获得可验证的业务查询结果",
            "data": {"agent_facts": facts},
        }
    original_tool = {
        "search_announcements": "announcement_list",
        "search_community_knowledge": "community_knowledge",
        "list_bills": "billing_query",
        "get_bill": "billing_query",
        "list_work_orders": "repair_list",
        "get_work_order": "repair_get",
        "list_inspection_tasks": "inspection_list",
        "get_inspection_task": "inspection_get_task",
        "list_security_events": "inspection_list",
        "get_security_event": "inspection_get_event",
    }.get(selected.tool, selected.tool)
    return {
        "ok": selected.ok,
        "tool": original_tool,
        "error_code": selected.error_code,
        "reason": selected.error_message or selected.error_code,
        "data": {**selected.data, "agent_facts": facts},
    }
