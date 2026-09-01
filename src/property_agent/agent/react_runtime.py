"""Governed observation-driven ReAct coordinator for one active domain goal."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

from property_agent.agent.orchestration import (
    ExecutionMode,
    GoalOutcome,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    SpecialistName,
    SpecialistOutcome,
)
from property_agent.agent.react_contracts import (
    ActiveGoalState,
    GoalStatus,
    ReactDecision,
    ReactDecisionType,
    ReactObservation,
)
from property_agent.agent.react_governance import ReactActionGovernance
from property_agent.agent.state import ProposedAction
from property_agent.platform.application.hashing import canonical_hash

_OBSERVATION_PRIVATE_FIELDS = frozenset(
    {
        "actor_id",
        "community_id",
        "house_id",
        "confirmation_token",
        "approval_ref",
        "idempotency_key",
        "lease",
        "fence",
    }
)


class ReactCoordinator:
    """Select and execute one governed capability at a time."""

    def __init__(
        self,
        gateway: Any,
        specialists: dict[Any, Any],
        *,
        fallback_planner: Any | None = None,
        observe=None,
    ) -> None:
        self._gateway = gateway
        self._specialists = specialists
        self._action_governance = ReactActionGovernance(specialists)
        self._fallback_planner = fallback_planner
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
        if state.active_goal is not None:
            return state.active_goal
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
        if goal is None or goal.last_decision is None:
            raise RuntimeError("ReAct action has no active decision")
        decision = goal.last_decision
        if decision.decision is not ReactDecisionType.ACT:
            raise RuntimeError("ReAct action requires ACT decision")
        rejection = self._action_governance.validate(goal, decision, runtime)
        if rejection:
            self._append_rejection(state, goal, decision, rejection)
            return
        explicit = self._action_step(goal, decision)
        specialist = self._specialists.get(explicit.specialist)
        if specialist is None:
            self._append_rejection(state, goal, decision, "SPECIALIST_NOT_CONFIGURED")
            return
        result = specialist.invoke(explicit, state, runtime, state.specialist_results)
        state.specialist_results = (*state.specialist_results, result)
        self._apply_result(state, goal, explicit, decision, result)

    @staticmethod
    def _action_step(goal, decision) -> PlanStep:
        specialists = {
            "repair": SpecialistName.REPAIR,
            "billing": SpecialistName.BILLING,
            "announcement": SpecialistName.ANNOUNCEMENT,
            "inspection": SpecialistName.INSPECTION,
        }
        return PlanStep(
            step_id=goal.goal_id,
            domain=goal.domain,
            specialist=specialists[goal.domain],
            goal=goal.goal,
            capability=decision.capability,
            parameters=dict(decision.arguments),
        )

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
        goal.last_public_message = result.public_message or goal.last_public_message
        state.pending_action = None
        state.proposed_action = None
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
            if state.plan is not None:
                state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)
            goal.last_public_message = decision.question or "请补充必要信息。"
            state.add_message("assistant", goal.last_public_message)
        elif decision.decision is ReactDecisionType.HANDOVER:
            self._handover(state, goal, decision.reason_code or "REACT_HANDOVER")
        else:
            self._finish(state, goal, decision)

    def _finish(self, state, goal, decision) -> None:
        requested = decision.requested_domain
        if requested and not self._transition_allowed(state, requested):
            goal.status = GoalStatus.NEEDS_CLARIFICATION
            if state.plan is not None:
                state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)
            state.missing_slots = ["requested_domain_authorization"]
            state.requested_slot = "requested_domain_authorization"
            state.add_message("assistant", "该领域不在本次任务的预授权范围内，请明确是否新增任务。")
            return
        goal.status = decision.goal_status
        goal.last_decision = None
        if state.plan is not None:
            step = self._current_step(state)
            if step is not None:
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
        legacy = state.legacy_plan
        if legacy is None and self._fallback_planner is not None:
            legacy = self._fallback_planner.create_plan(state, runtime)
        if legacy is None:
            self._handover(state, goal, "LEGACY_PLAN_UNAVAILABLE")
            return None
        state.legacy_plan = legacy
        state.plan = replace(legacy, replan_reason=reason)
        self._observe("react_fallback", {"domain": goal.domain, "reason": reason})
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
    def _guard_decision(goal, runtime) -> ReactDecision | None:
        if goal.action_count < runtime.execution_policy.max_react_actions:
            return None
        return ReactDecision(
            ReactDecisionType.HANDOVER,
            GoalStatus.HANDOVER,
            reason_code="MAX_REACT_ACTIONS_EXCEEDED",
        )

    def _context(self, state, runtime, goal) -> dict[str, Any]:
        allowed = self._action_governance.effective_allowlist(goal, runtime)
        specialist = self._specialists.get(
            {
                "repair": SpecialistName.REPAIR,
                "billing": SpecialistName.BILLING,
                "announcement": SpecialistName.ANNOUNCEMENT,
                "inspection": SpecialistName.INSPECTION,
            }.get(goal.domain)
        )
        inventory = tuple(getattr(specialist, "capability_inventory", ()))
        return {
            "goal_id": goal.goal_id,
            "goal": goal.goal,
            "domain": goal.domain,
            "candidate_facts": goal.candidate_facts,
            "observations": [item.to_dict() for item in goal.observations],
            "allowed_capabilities": sorted(allowed),
            "capability_inventory": [item for item in inventory if item.get("name") in allowed],
            "business_date": str(state.trusted_context.get("business_date") or date.today()),
            "remaining_action_budget": runtime.execution_policy.max_react_actions
            - goal.action_count,
            "preauthorized_domains": sorted(
                goal.authorized_domains
                or (
                    {step.domain for step in state.plan.steps}
                    if state.plan is not None
                    else {goal.domain}
                )
            ),
        }

    @staticmethod
    def _bounded_data(data: dict[str, Any]) -> dict[str, Any]:
        return {
            key: ReactCoordinator._bounded_value(value)
            for key, value in data.items()
            if key not in _OBSERVATION_PRIVATE_FIELDS
        }

    @staticmethod
    def _bounded_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ReactCoordinator._bounded_value(item)
                for key, item in value.items()
                if key not in _OBSERVATION_PRIVATE_FIELDS
            }
        if isinstance(value, list | tuple):
            return [ReactCoordinator._bounded_value(item) for item in value[:20]]
        return value[:2000] if isinstance(value, str) else value

    @staticmethod
    def _current_step(state):
        if state.plan is None or state.plan.current_step_id is None:
            return None
        return next((s for s in state.plan.steps if s.step_id == state.plan.current_step_id), None)

    @staticmethod
    def _transition_allowed(state, domain) -> bool:
        if state.active_goal is not None and state.plan is None:
            return domain in state.active_goal.authorized_domains
        return any(
            step.domain == domain and step.status is PlanStepStatus.PENDING
            for step in state.plan.steps
        )

    def _wait_confirmation(self, state, goal, step, result) -> None:
        parameters = dict(result.data["parameters"])
        params_hash = str(result.data["params_hash"])
        issued_at = datetime.now(timezone.utc).isoformat()
        state.proposed_action = ProposedAction(
            result.capability, parameters, params_hash, issued_at
        )
        pending = {
            "tool": result.capability,
            "params": parameters,
            "params_hash": params_hash,
            "issued_at": issued_at,
            "goal_id": goal.goal_id,
        }
        if state.plan is not None:
            pending.update(plan_id=state.plan.plan_id, plan_step_id=step.step_id)
        state.pending_action = pending
        goal.status = GoalStatus.WAITING_CONFIRMATION
        goal.last_public_message = result.public_message
        if state.plan is not None:
            state.plan = state.plan.replace_step(
                replace(step, status=PlanStepStatus.PENDING_CONFIRMATION)
            )
            state.plan = replace(state.plan, status=PlanStatus.WAITING_CONFIRMATION)

    def _clarify_from_result(self, state, goal, result) -> None:
        goal.status = GoalStatus.NEEDS_CLARIFICATION
        goal.missing_information = result.missing_inputs
        state.missing_slots = list(result.missing_inputs)
        state.requested_slot = result.missing_inputs[0] if result.missing_inputs else None
        goal.last_public_message = result.public_message
        if state.plan is not None:
            state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)

    def _handover(self, state, goal, reason) -> None:
        goal.status = GoalStatus.HANDOVER
        goal.handover = True
        goal.last_decision = None
        state.handover_required = True
        state.error = reason
        if state.plan is not None:
            state.plan = replace(state.plan, status=PlanStatus.HANDOVER, replan_reason=reason)

    def _emit(self, goal, decision) -> None:
        self._observe(
            "react_decision",
            {"domain": goal.domain, **decision.trace_dict(), "actions": goal.action_count},
        )
