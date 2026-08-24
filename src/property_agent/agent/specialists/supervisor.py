"""Governed Supervisor for sequential PR5 specialist orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from property_agent.agent.orchestration import (
    GoalOutcome,
    ObjectiveClassification,
    OrchestrationBudget,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    PlanValidator,
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.planning_contracts import RelevanceDecision
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.state import AgentState, ProposedAction
from property_agent.agent.working_state import domain_from_legacy


class Supervisor:
    """Plan, delegate, evaluate, and synthesize without business authority."""

    def __init__(
        self,
        planner: SupervisorPlanner,
        specialists: dict[SpecialistName, Any],
        *,
        validator: PlanValidator | None = None,
        clock: Callable[[], datetime] | None = None,
        observe: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._planner = planner
        self._specialists = dict(specialists)
        self._validator = validator or PlanValidator()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._observe = observe or (lambda _event, _fields: None)

    def prepare(self, state: AgentState, runtime: RuntimeContext) -> AgentState:
        now = self._clock()
        duration = timedelta(seconds=runtime.execution_policy.plan_duration_seconds)
        if state.plan is None:
            state.plan = self._planner.create_plan(state, runtime)
            self._set_objective_context(state)
            state.orchestration_budget = OrchestrationBudget.start(now=now, duration=duration)
            self._emit(
                "supervisor_plan_created",
                {
                    "plan_id": state.plan.plan_id,
                    "classification": state.plan.objective_classification.value,
                    "step_count": len(state.plan.steps),
                },
            )
        elif state.orchestration_budget is not None:
            state.orchestration_budget = state.orchestration_budget.resume(
                now=now, server_ceiling=duration
            )
        if self._budget_expired(state, now):
            return self._fail_plan(state, "EXECUTION_DEADLINE_EXCEEDED")
        if self._limit_reached(state, runtime, "supervisor_steps", "max_supervisor_steps"):
            return self._fail_plan(state, "MAX_SUPERVISOR_STEPS_EXCEEDED")
        self._increment_budget(state, supervisor_steps=1)
        if state.plan.status == PlanStatus.WAITING_CONFIRMATION:
            return state
        if state.plan.status != PlanStatus.ACTIVE:
            return state
        if state.plan.objective_classification == ObjectiveClassification.GENERAL_HELP:
            state.plan = replace(state.plan, status=PlanStatus.COMPLETED, current_step_id=None)
            return state
        if state.plan.objective_classification == ObjectiveClassification.UNCERTAIN:
            state.plan = replace(
                state.plan, status=PlanStatus.NEEDS_CLARIFICATION, current_step_id=None
            )
            return state
        self._select_next_eligible(state)
        return state

    def current_step(self, state: AgentState) -> PlanStep | None:
        plan = state.plan
        if plan is None or plan.current_step_id is None:
            return None
        return next((step for step in plan.steps if step.step_id == plan.current_step_id), None)

    def run_current(self, state: AgentState, runtime: RuntimeContext) -> SpecialistResult:
        step = self.current_step(state)
        if step is None or step.status != PlanStepStatus.PENDING:
            raise RuntimeError("no pending Supervisor step is eligible")
        if self._limit_reached(state, runtime, "delegations", "max_delegations"):
            self._fail_plan(state, "MAX_DELEGATIONS_EXCEEDED")
            return self._budget_result(step, "MAX_DELEGATIONS_EXCEEDED")
        cross_domain = self._is_cross_domain_step(state, step)
        if cross_domain and self._limit_reached(
            state, runtime, "cross_domain_steps", "max_cross_domain_steps"
        ):
            self._fail_plan(state, "MAX_CROSS_DOMAIN_STEPS_EXCEEDED")
            return self._budget_result(step, "MAX_CROSS_DOMAIN_STEPS_EXCEEDED")
        specialist = self._specialists.get(step.specialist)
        if specialist is None:
            result = SpecialistResult(
                SpecialistOutcome.UNSUPPORTED,
                step.step_id,
                step.specialist,
                capability=step.capability,
                reason_code="SPECIALIST_NOT_CONFIGURED",
                public_message="该领域能力暂不可用。",
            )
        else:
            changes = {"delegations": 1}
            if cross_domain:
                changes["cross_domain_steps"] = 1
            self._increment_budget(state, **changes)
            self._emit(
                "specialist_delegated",
                {"specialist": step.specialist.value, "capability": step.capability},
            )
            result = specialist.invoke(step, state, runtime, state.specialist_results)
        self.apply_result(state, result, runtime)
        return result

    def apply_result(
        self,
        state: AgentState,
        result: SpecialistResult,
        runtime: RuntimeContext,
    ) -> None:
        step = self.current_step(state)
        if step is None or result.step_id != step.step_id:
            raise ValueError("specialist result does not match the current plan step")
        state.specialist_results = (*state.specialist_results, result)
        self._record_capability_progress(state, result)
        if result.outcome == SpecialistOutcome.SUCCESS:
            self._complete_step(state, step, result)
        elif result.outcome == SpecialistOutcome.HITL_REQUIRED:
            self._wait_for_confirmation(state, step, result)
        elif result.outcome == SpecialistOutcome.REPLAN:
            self._replan(state, step, result, runtime)
        elif result.outcome == SpecialistOutcome.NEEDS_CLARIFICATION:
            if self._limit_reached(
                state, runtime, "clarification_loops", "max_clarification_loops"
            ):
                self._fail_plan(state, "MAX_CLARIFICATION_LOOPS_EXCEEDED")
                return
            self._increment_budget(state, clarification_loops=1)
            self._mark_step(state, step, PlanStepStatus.NEEDS_CLARIFICATION)
            state.goal_outcomes[step.step_id] = GoalOutcome.NEEDS_CLARIFICATION
            state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)
            state.missing_slots = list(result.missing_inputs)
            state.requested_slot = result.missing_inputs[0] if result.missing_inputs else None
        elif result.outcome == SpecialistOutcome.HANDOVER:
            self._mark_step(state, step, PlanStepStatus.HANDOVER)
            state.goal_outcomes[step.step_id] = GoalOutcome.HANDOVER
            state.plan = replace(state.plan, status=PlanStatus.HANDOVER)
            state.handover_required = True
        else:
            self._mark_step(state, step, PlanStepStatus.FAILED)
            state.goal_outcomes[step.step_id] = GoalOutcome.FAILED
            state.tool_result = {
                "ok": False,
                "tool": result.capability,
                "error": {"code": result.reason_code, "message": result.public_message},
            }
        if result.public_message:
            state.add_message("assistant", result.public_message)
        self._emit(
            "specialist_completed",
            {
                "specialist": result.specialist.value,
                "capability": result.capability,
                "outcome": result.outcome.value,
                "reason": result.reason_code,
            },
        )

    def cancel_current(self, state: AgentState) -> None:
        step = self.current_step(state)
        if step is not None:
            self._mark_step(state, step, PlanStepStatus.SKIPPED)
            state.goal_outcomes[step.step_id] = GoalOutcome.FAILED
        state.pending_action = None
        state.proposed_action = None
        state._resume = None
        if state.plan is not None:
            state.plan = replace(state.plan, status=PlanStatus.ACTIVE)
        state.add_message("assistant", "已取消当前待确认操作，未执行该业务写入。")

    def synthesize(self, state: AgentState) -> str:
        plan = state.plan
        if plan is None:
            return "当前没有可汇总的任务。"
        if plan.objective_classification == ObjectiveClassification.GENERAL_HELP:
            return "我可以协助报修、账单、公告和巡检安防事务。涉及写入时会逐项请您确认。"
        if plan.objective_classification == ObjectiveClassification.UNCERTAIN:
            return "请说明您要查询或办理的是报修、账单、公告还是巡检安防事项。"
        labels = {
            GoalOutcome.COMPLETED: "已完成",
            GoalOutcome.CONDITION_NOT_MET: "条件未满足，未执行",
            GoalOutcome.PENDING_CONFIRMATION: "待确认",
            GoalOutcome.NEEDS_CLARIFICATION: "需补充信息",
            GoalOutcome.FAILED: "失败",
            GoalOutcome.HANDOVER: "需人工处理",
        }
        parts = []
        for step in plan.steps:
            outcome = state.goal_outcomes.get(step.step_id)
            if outcome is not None:
                parts.append(f"{step.goal}：{labels[outcome]}")
        return "；".join(parts) if parts else "任务尚未产生可核验结果。"

    def _select_next_eligible(self, state: AgentState) -> None:
        while True:
            pending = next(
                (
                    step
                    for step in state.plan.steps
                    if step.status == PlanStepStatus.PENDING
                    and self._dependencies_completed(state, step)
                ),
                None,
            )
            if pending is None:
                self._finish_plan(state)
                return
            if self._condition_allows(state, pending):
                state.plan = replace(state.plan, current_step_id=pending.step_id)
                return
            self._mark_step(state, pending, PlanStepStatus.SKIPPED)
            state.goal_outcomes[pending.step_id] = GoalOutcome.CONDITION_NOT_MET

    @staticmethod
    def _dependencies_completed(state: AgentState, step: PlanStep) -> bool:
        by_id = {item.step_id: item for item in state.plan.steps}
        return all(
            by_id[dependency].status in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
            for dependency in step.dependencies
        )

    def _condition_allows(self, state: AgentState, step: PlanStep) -> bool:
        if step.condition is None:
            return True
        prior = next(
            (
                result
                for result in reversed(state.specialist_results)
                if result.step_id in step.dependencies
            ),
            None,
        )
        if prior is None:
            return False
        if step.condition == "if_no_equivalent_active_repair":
            return not self._equivalent_active_repair(prior.data, step.parameters)
        if step.condition == "if_relevant_inspection_issue":
            return (
                self._planner.relevant_issue_decision(step, prior.data) is RelevanceDecision.MATCH
            )
        return False

    @staticmethod
    def _equivalent_active_repair(data: dict[str, Any], parameters: dict[str, Any]) -> bool:
        location = str(parameters.get("location") or "").strip()
        terminal = {"COMPLETED", "CANCELLED", "CLOSED", "REJECTED"}
        for item in data.get("items") or ():
            if str(item.get("status") or "").upper() in terminal:
                continue
            if location and str(item.get("location") or "").strip() == location:
                return True
        return False

    def _complete_step(self, state, step, result) -> None:
        self._mark_step(state, step, PlanStepStatus.COMPLETED, result_reference=result.capability)
        state.goal_outcomes[step.step_id] = GoalOutcome.COMPLETED
        state.pending_action = None
        state.proposed_action = None
        state.tool_result = {"ok": True, "tool": result.capability, "data": result.data}
        if state.plan.status == PlanStatus.WAITING_CONFIRMATION:
            state.plan = replace(state.plan, status=PlanStatus.ACTIVE)

    def _wait_for_confirmation(self, state, step, result) -> None:
        parameters = dict(result.data["parameters"])
        params_hash = str(result.data["params_hash"])
        issued_at = self._clock().isoformat()
        state.proposed_action = ProposedAction(
            result.capability, parameters, params_hash, issued_at
        )
        state.pending_action = {
            "tool": result.capability,
            "params": parameters,
            "params_hash": params_hash,
            "issued_at": issued_at,
            "plan_id": state.plan.plan_id,
            "plan_step_id": step.step_id,
        }
        state.operation_level = result.data.get("operation_level")
        self._mark_step(state, step, PlanStepStatus.PENDING_CONFIRMATION)
        state.goal_outcomes[step.step_id] = GoalOutcome.PENDING_CONFIRMATION
        state.plan = replace(state.plan, status=PlanStatus.WAITING_CONFIRMATION)

    def _replan(self, state, step, result, runtime) -> None:
        if self._limit_reached(state, runtime, "replans", "max_replans"):
            self._fail_plan(state, "MAX_REPLANS_EXCEEDED")
            return
        capability = result.data.get("replacement_capability")
        parameters = dict(result.data.get("replacement_parameters") or {})
        if capability == step.capability and parameters == step.parameters:
            self._fail_plan(state, "REPLAN_MADE_NO_PROGRESS")
            return
        replacement = replace(
            step,
            capability=capability,
            parameters=parameters,
            status=PlanStepStatus.PENDING,
        )
        candidate = state.plan.replace_step(replacement)
        candidate = replace(candidate, replan_reason=result.reason_code)
        state.plan = self._validator.validate(candidate)
        self._increment_budget(state, replans=1)

    def _finish_plan(self, state: AgentState) -> None:
        outcomes = set(state.goal_outcomes.values())
        non_failures = {GoalOutcome.COMPLETED, GoalOutcome.CONDITION_NOT_MET}
        if GoalOutcome.FAILED in outcomes and outcomes & non_failures:
            status = PlanStatus.PARTIAL
        elif GoalOutcome.FAILED in outcomes:
            status = PlanStatus.FAILED
        else:
            status = PlanStatus.COMPLETED
        state.plan = replace(state.plan, status=status, current_step_id=None)

    def _fail_plan(self, state: AgentState, reason: str) -> AgentState:
        if state.plan is not None:
            state.plan = replace(state.plan, status=PlanStatus.FAILED, replan_reason=reason)
        state.error = reason
        self._emit("supervisor_budget_exhausted", {"reason": reason})
        return state

    @staticmethod
    def _mark_step(state, step, status, *, result_reference=None) -> None:
        updated = replace(step, status=status, result_reference=result_reference)
        state.plan = state.plan.replace_step(updated)

    def _budget_expired(self, state, now) -> bool:
        return bool(state.orchestration_budget and state.orchestration_budget.expired(now))

    @staticmethod
    def _limit_reached(state, runtime, counter, limit) -> bool:
        budget = state.orchestration_budget
        return bool(budget and getattr(budget, counter) >= getattr(runtime.execution_policy, limit))

    @staticmethod
    def _increment_budget(state, **changes) -> None:
        budget = state.orchestration_budget
        if budget is None:
            return
        state.orchestration_budget = replace(
            budget, **{name: getattr(budget, name) + value for name, value in changes.items()}
        )

    @staticmethod
    def _record_capability_progress(state, result) -> None:
        if result.outcome == SpecialistOutcome.HITL_REQUIRED:
            return
        invocation = state.capability_invocation
        fingerprints = invocation.prior_fingerprints
        if result.fingerprint:
            fingerprints = frozenset((*fingerprints, result.fingerprint))
        state.capability_invocation = replace(
            invocation,
            step=invocation.step + 1,
            calls_made=invocation.calls_made + 1,
            fingerprint=result.fingerprint,
            prior_fingerprints=fingerprints,
        )

    @staticmethod
    def _is_cross_domain_step(state: AgentState, step: PlanStep) -> bool:
        prior = next(
            (item for item in reversed(state.specialist_results) if item.step_id != step.step_id),
            None,
        )
        return bool(prior and prior.specialist != step.specialist)

    @staticmethod
    def _budget_result(step, reason):
        return SpecialistResult(
            SpecialistOutcome.BUDGET_EXHAUSTED,
            step.step_id,
            step.specialist,
            capability=step.capability,
            reason_code=reason,
            public_message="本轮执行预算已用尽。",
        )

    def _emit(self, event: str, fields: dict[str, Any]) -> None:
        try:
            self._observe(event, fields)
        except Exception:
            return

    @staticmethod
    def _set_objective_context(state: AgentState) -> None:
        plan = state.plan
        if plan.steps:
            intent = plan.steps[0].domain.upper()
        elif plan.objective_classification == ObjectiveClassification.GENERAL_HELP:
            intent = "GENERAL_HELP"
        else:
            intent = "UNCERTAIN"
        state.intent = intent
        state.domain = domain_from_legacy(intent, state.slots)
