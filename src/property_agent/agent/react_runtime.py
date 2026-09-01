"""Governed observation-driven ReAct coordinator for one active domain goal."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from property_agent.agent.orchestration import (
    ExecutionMode,
    GoalOutcome,
    PlanStatus,
    PlanStepStatus,
    SpecialistOutcome,
)
from property_agent.agent.react_contracts import (
    ActiveGoalState,
    GoalStatus,
    ReactDecision,
    ReactDecisionType,
    ReactObservation,
)
from property_agent.agent.state import ProposedAction
from property_agent.platform.application.hashing import canonical_hash

DOMAIN_ALLOWLISTS = {
    "repair": frozenset({"repair_list", "repair_get", "repair_create"}),
    "billing": frozenset({"billing_query", "billing_consult"}),
    "announcement": frozenset(
        {
            "announcement_list",
            "announcement_get",
            "community_knowledge_search",
            "announcement_draft",
            "announcement_revise",
            "announcement_create_draft",
            "announce_publish",
            "announcement_schedule_publish",
        }
    ),
    "inspection": frozenset(
        {
            "inspection_list",
            "inspection_get_task",
            "inspection_get_event",
            "inspection_create",
            "inspection_start_task",
            "inspection_add_record",
            "inspection_submit_records",
            "inspection_ai_suggest",
            "security_event_create",
            "security_event_submit_disposal",
            "close_high_risk_event",
        }
    ),
}


class ReactCoordinator:
    """Select and execute one governed capability at a time."""

    def __init__(self, gateway: Any, specialists: dict[Any, Any], observe=None) -> None:
        self._gateway = gateway
        self._specialists = specialists
        self._observe = observe or (lambda _event, _fields: None)

    def enable(self, state: Any, runtime: Any) -> None:
        if state.plan is None or state.plan.execution_mode is ExecutionMode.REACT:
            return
        enabled = runtime.execution_policy.react_domains
        if not enabled or not any(step.domain in enabled for step in state.plan.steps):
            return
        state.legacy_plan = state.plan
        steps = tuple(
            replace(step, capability=None) if step.domain in enabled else step
            for step in state.plan.steps
        )
        state.plan = replace(state.plan, steps=steps, execution_mode=ExecutionMode.REACT)

    def ensure_goal(self, state: Any) -> ActiveGoalState | None:
        step = self._current_step(state)
        if step is None or step.capability is not None:
            return None
        if state.active_goal is None or state.active_goal.goal_id != step.step_id:
            state.active_goal = ActiveGoalState(
                goal_id=step.step_id,
                goal=step.goal,
                domain=step.domain,
                candidate_facts=dict(step.parameters),
            )
        return state.active_goal

    @staticmethod
    def resume_clarification(state: Any) -> None:
        goal = state.active_goal
        if (
            goal is None
            or state.plan is None
            or state.plan.status is not PlanStatus.NEEDS_CLARIFICATION
            or not state._continuation
        ):
            return
        goal.candidate_facts.update(state.slots)
        goal.status = GoalStatus.IN_PROGRESS
        goal.missing_information = ()
        goal.last_decision = None
        state.missing_slots = []
        state.requested_slot = None
        state.plan = replace(state.plan, status=PlanStatus.ACTIVE)

    @staticmethod
    def cancel(state: Any) -> None:
        if state.active_goal is not None:
            state.active_goal.status = GoalStatus.CANCELLED
            state.active_goal.last_decision = None

    def reason(self, state: Any, runtime: Any) -> ReactDecision | None:
        goal = self.ensure_goal(state)
        if goal is None:
            return None
        guard = self._guard_decision(goal, runtime)
        if guard is not None:
            decision = guard
        else:
            try:
                decision = self._gateway.react_decide(self._context(state, runtime, goal))
                if not isinstance(decision, ReactDecision):
                    decision = ReactDecision.from_dict(decision)
            except Exception:
                return self._fallback(state, runtime, "REACT_PROVIDER_OR_SCHEMA_FAILED")
        goal.last_decision = decision
        self._apply_non_action(state, goal, decision, runtime)
        self._emit(goal, decision)
        return decision

    def action(self, state: Any, runtime: Any) -> None:
        goal = self.ensure_goal(state)
        step = self._current_step(state)
        if goal is None or step is None or goal.last_decision is None:
            raise RuntimeError("ReAct action has no active decision")
        decision = goal.last_decision
        if decision.decision is not ReactDecisionType.ACT:
            raise RuntimeError("ReAct action requires ACT decision")
        rejection = self._validate_action(state, runtime, goal, decision)
        if rejection:
            self._append_rejection(state, goal, decision, rejection)
            return
        explicit = replace(step, capability=decision.capability, parameters=decision.arguments)
        specialist = self._specialists.get(step.specialist)
        result = specialist.invoke(explicit, state, runtime, state.specialist_results)
        state.specialist_results = (*state.specialist_results, result)
        self._apply_result(state, goal, step, decision, result)

    def _apply_result(self, state, goal, step, decision, result) -> None:
        params_hash = canonical_hash(decision.arguments)
        if result.outcome is SpecialistOutcome.HITL_REQUIRED:
            self._wait_confirmation(state, goal, step, result)
            return
        observation = ReactObservation(
            capability=decision.capability or "",
            ok=result.outcome is SpecialistOutcome.SUCCESS,
            params_hash=params_hash,
            result_fingerprint=result.fingerprint or canonical_hash(result.data),
            error_code=result.reason_code,
            data=self._bounded_data(result.data),
        )
        goal.append_observation(observation)
        goal.action_count += 1
        goal.last_action = decision.capability
        goal.last_decision = None
        state.tool_result = {
            "ok": observation.ok,
            "tool": observation.capability,
            "data": observation.data,
        }
        if result.outcome is SpecialistOutcome.NEEDS_CLARIFICATION:
            self._clarify_from_result(state, goal, result)
        elif result.outcome is SpecialistOutcome.HANDOVER:
            self._handover(state, goal, result.reason_code or "HUMAN_ONLY")

    def _apply_non_action(self, state, goal, decision, runtime) -> None:
        if decision.decision is ReactDecisionType.ACT:
            return
        if decision.decision is ReactDecisionType.CLARIFY:
            goal.clarification_count += 1
            if goal.clarification_count > runtime.execution_policy.max_react_clarifications:
                self._handover(state, goal, "MAX_CLARIFICATIONS_EXCEEDED")
                return
            goal.status = GoalStatus.NEEDS_CLARIFICATION
            goal.missing_information = decision.missing_information
            state.missing_slots = list(decision.missing_information)
            state.requested_slot = decision.missing_information[0]
            state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)
            state.add_message("assistant", decision.question or "请补充必要信息。")
        elif decision.decision is ReactDecisionType.HANDOVER:
            self._handover(state, goal, decision.reason_code or "REACT_HANDOVER")
        else:
            self._finish(state, goal, decision)

    def _finish(self, state, goal, decision) -> None:
        step = self._current_step(state)
        requested = decision.requested_domain
        if requested and not self._transition_allowed(state, requested):
            goal.status = GoalStatus.NEEDS_CLARIFICATION
            state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)
            state.missing_slots = ["requested_domain_authorization"]
            state.requested_slot = "requested_domain_authorization"
            state.add_message("assistant", "该领域不在本次任务的预授权范围内，请明确是否新增任务。")
            return
        goal.status = decision.goal_status
        goal.last_decision = None
        state.goal_outcomes[step.step_id] = GoalOutcome.COMPLETED
        state.plan = state.plan.replace_step(replace(step, status=PlanStepStatus.COMPLETED))
        state.plan = replace(state.plan, status=PlanStatus.ACTIVE, current_step_id=None)

    def _fallback(self, state, runtime, reason) -> None:
        goal = self.ensure_goal(state)
        if not runtime.execution_policy.react_fallback_enabled or goal.fallback_used:
            self._handover(state, goal, reason)
            return None
        goal.degraded = True
        goal.fallback_used = True
        if state.legacy_plan is None:
            self._handover(state, goal, "LEGACY_PLAN_UNAVAILABLE")
            return None
        state.plan = replace(state.legacy_plan, replan_reason=reason)
        self._observe("react_fallback", {"domain": goal.domain, "reason": reason})
        return None

    def _validate_action(self, state, runtime, goal, decision) -> str | None:
        allowed = DOMAIN_ALLOWLISTS.get(goal.domain, frozenset())
        if decision.capability not in allowed:
            return "CAPABILITY_NOT_IN_SPECIALIST_ALLOWLIST"
        if runtime.execution_policy.allowlist is not None:
            if decision.capability not in runtime.execution_policy.allowlist:
                return "CAPABILITY_NOT_IN_RUNTIME_ALLOWLIST"
        if self._repeat_count(goal, decision) >= runtime.execution_policy.max_react_repeats:
            return "REACT_NO_PROGRESS"
        if decision.capability == "repair_create":
            repair_guard = self._repair_create_guard(goal, decision)
            if repair_guard:
                return repair_guard
        if goal.domain == "inspection" and self._inspection_preread_required(goal, decision):
            return "INSPECTION_PREREAD_REQUIRED"
        if decision.capability == "billing_consult" and not self._billing_rule_missing(goal):
            return "BILLING_RULE_NOT_PROVEN_MISSING"
        return None

    def _append_rejection(self, state, goal, decision, code) -> None:
        observation = ReactObservation(
            capability=decision.capability or "",
            ok=False,
            params_hash=canonical_hash(decision.arguments),
            result_fingerprint=canonical_hash({"reason": code}),
            error_code=code,
        )
        goal.append_observation(observation)
        goal.action_count += 1
        goal.last_decision = None
        if code == "REACT_NO_PROGRESS":
            self._handover(state, goal, code)
        self._observe("react_action_rejected", {"domain": goal.domain, "reason": code})

    @staticmethod
    def _repair_create_guard(goal, decision) -> str | None:
        for item in goal.observations:
            if item.capability != "repair_list" or not item.ok:
                continue
            requested_location = str(decision.arguments.get("location") or "").strip()
            requested_category = str(decision.arguments.get("category") or "").strip()
            if (
                requested_location
                and str(item.data.get("query_location") or "") != requested_location
            ):
                continue
            if (
                requested_category
                and str(item.data.get("query_category") or "") != requested_category
            ):
                continue
            terminal = {"COMPLETED", "CANCELLED", "CLOSED", "REJECTED"}
            if any(
                str(value.get("status") or "").upper() not in terminal
                for value in item.data.get("items") or ()
            ):
                return "ACTIVE_REPAIR_EXISTS"
            return None
        return "REPAIR_PREREAD_REQUIRED"

    @staticmethod
    def _inspection_preread_required(goal, decision) -> bool:
        write_capabilities = {
            "inspection_start_task",
            "inspection_add_record",
            "inspection_submit_records",
            "security_event_submit_disposal",
            "close_high_risk_event",
        }
        asks_existing = any(
            term in goal.goal for term in ("查已有", "现有", "已有事件", "check existing")
        )
        if decision.capability not in write_capabilities or not asks_existing:
            return False
        reads = {"inspection_list", "inspection_get_task", "inspection_get_event"}
        return not any(item.ok and item.capability in reads for item in goal.observations)

    @staticmethod
    def _billing_rule_missing(goal) -> bool:
        return any(
            item.capability == "billing_query" and item.ok and item.data.get("rule") is None
            for item in goal.observations
        )

    @staticmethod
    def _repeat_count(goal, decision) -> int:
        target = (decision.capability, canonical_hash(decision.arguments))
        return sum((item.capability, item.params_hash) == target for item in goal.observations)

    @staticmethod
    def _guard_decision(goal, runtime) -> ReactDecision | None:
        if goal.action_count < runtime.execution_policy.max_react_actions:
            return None
        return ReactDecision(
            ReactDecisionType.HANDOVER,
            GoalStatus.HANDOVER,
            reason_code="MAX_REACT_ACTIONS_EXCEEDED",
        )

    @staticmethod
    def _context(state, runtime, goal) -> dict[str, Any]:
        allowed = DOMAIN_ALLOWLISTS.get(goal.domain, frozenset())
        if runtime.execution_policy.allowlist is not None:
            allowed &= runtime.execution_policy.allowlist
        return {
            "goal_id": goal.goal_id,
            "goal": goal.goal,
            "domain": goal.domain,
            "candidate_facts": goal.candidate_facts,
            "observations": [item.to_dict() for item in goal.observations],
            "allowed_capabilities": sorted(allowed),
            "remaining_action_budget": runtime.execution_policy.max_react_actions
            - goal.action_count,
            "preauthorized_domains": sorted({step.domain for step in state.plan.steps}),
        }

    @staticmethod
    def _bounded_data(data: dict[str, Any]) -> dict[str, Any]:
        bounded = dict(data)
        for key, value in tuple(bounded.items()):
            if isinstance(value, list):
                bounded[key] = value[:20]
            elif isinstance(value, str):
                bounded[key] = value[:2000]
        return bounded

    @staticmethod
    def _current_step(state):
        if state.plan is None or state.plan.current_step_id is None:
            return None
        return next((s for s in state.plan.steps if s.step_id == state.plan.current_step_id), None)

    @staticmethod
    def _transition_allowed(state, domain) -> bool:
        return any(
            step.domain == domain and step.status is PlanStepStatus.PENDING
            for step in state.plan.steps
        )

    def _wait_confirmation(self, state, goal, step, result) -> None:
        parameters = dict(result.data["parameters"])
        params_hash = str(result.data["params_hash"])
        state.proposed_action = ProposedAction(result.capability, parameters, params_hash)
        state.pending_action = {
            "tool": result.capability,
            "params": parameters,
            "params_hash": params_hash,
            "plan_id": state.plan.plan_id,
            "plan_step_id": step.step_id,
            "goal_id": goal.goal_id,
        }
        goal.status = GoalStatus.WAITING_CONFIRMATION
        state.plan = state.plan.replace_step(
            replace(step, status=PlanStepStatus.PENDING_CONFIRMATION)
        )
        state.plan = replace(state.plan, status=PlanStatus.WAITING_CONFIRMATION)

    def _clarify_from_result(self, state, goal, result) -> None:
        goal.status = GoalStatus.NEEDS_CLARIFICATION
        goal.missing_information = result.missing_inputs
        state.missing_slots = list(result.missing_inputs)
        state.requested_slot = result.missing_inputs[0] if result.missing_inputs else None
        state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)

    def _handover(self, state, goal, reason) -> None:
        goal.status = GoalStatus.HANDOVER
        goal.handover = True
        goal.last_decision = None
        state.handover_required = True
        state.error = reason
        state.plan = replace(state.plan, status=PlanStatus.HANDOVER, replan_reason=reason)

    def _emit(self, goal, decision) -> None:
        self._observe(
            "react_decision",
            {"domain": goal.domain, **decision.trace_dict(), "actions": goal.action_count},
        )
