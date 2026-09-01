"""Semantic Goal/domain resolution before Supervisor governance."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any
from uuid import uuid4

from property_agent.agent.goal_contracts import GoalResolution, GoalResolutionType
from property_agent.agent.react_contracts import ActiveGoalState, GoalStatus
from property_agent.agent.working_state import domain_from_legacy, project_domain_to_legacy_slots


class GoalResolutionError(RuntimeError):
    """The bounded semantic resolver was unavailable or returned an invalid contract."""


class GoalResolver:
    """Resolve Goal ownership and domain without selecting a capability."""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    def resolve(self, state: Any, runtime: Any) -> GoalResolution:
        method = getattr(self._gateway, "resolve_goal", None)
        if method is None:
            raise GoalResolutionError("semantic Goal resolver is unavailable")
        try:
            value = method(self._context(state, runtime))
            resolution = (
                value if isinstance(value, GoalResolution) else GoalResolution.from_dict(value)
            )
        except Exception as exc:
            raise GoalResolutionError("semantic Goal resolver failed") from exc
        self._apply(state, resolution)
        return resolution

    @staticmethod
    def _context(state: Any, runtime: Any) -> dict[str, Any]:
        active = state.active_goal
        return {
            "user_text": str(state.slots.get("user_text") or ""),
            "conversation": list(state.messages[-12:]),
            "active_goal": (
                {
                    "goal_id": active.goal_id,
                    "goal": active.goal,
                    "domain": active.domain,
                    "status": active.status.value,
                    "candidate_facts": deepcopy(active.candidate_facts),
                    "missing_information": list(active.missing_information),
                    "observations": [item.to_dict() for item in active.observations],
                }
                if active is not None
                else None
            ),
            "allowed_domains": sorted(runtime.execution_policy.react_domains),
            "business_date": str(state.trusted_context.get("business_date") or date.today()),
        }

    @staticmethod
    def _apply(state: Any, resolution: GoalResolution) -> None:
        state.goal_resolution_pending = False
        state.goal_resolution_kind = resolution.resolution.value
        state.goal_resolution_message = resolution.question
        if resolution.resolution is GoalResolutionType.CANCEL:
            if state.active_goal is not None:
                state.active_goal.status = GoalStatus.CANCELLED
                state.active_goal.last_decision = None
            state.pending_action = None
            state.proposed_action = None
            return
        if resolution.resolution in {
            GoalResolutionType.GENERAL_HELP,
            GoalResolutionType.UNCERTAIN,
        }:
            state.intent = (
                "GENERAL_HELP"
                if resolution.resolution is GoalResolutionType.GENERAL_HELP
                else "UNCERTAIN"
            )
            if resolution.resolution is GoalResolutionType.GENERAL_HELP:
                state.active_goal = None
            elif state.active_goal is not None:
                state.active_goal.status = GoalStatus.NEEDS_CLARIFICATION
                state.active_goal.last_public_message = resolution.question
            return
        if resolution.resolution is GoalResolutionType.CONTINUE and state.active_goal is not None:
            goal = state.active_goal
            if goal.domain != resolution.domain:
                raise GoalResolutionError("CONTINUE changed active Goal domain")
            goal.goal = str(resolution.goal)
            goal.candidate_facts.update(resolution.candidate_facts)
            goal.authorized_domains = tuple(
                dict.fromkeys((*goal.authorized_domains, *resolution.authorized_domains))
            )
            goal.constraints = tuple(dict.fromkeys((*goal.constraints, *resolution.constraints)))
            goal.status = GoalStatus.IN_PROGRESS
            goal.missing_information = ()
            goal.last_decision = None
        else:
            state.active_goal = ActiveGoalState(
                goal_id=f"goal-{uuid4()}",
                goal=str(resolution.goal),
                domain=str(resolution.domain),
                candidate_facts=dict(resolution.candidate_facts),
                authorized_domains=resolution.authorized_domains or (str(resolution.domain),),
                constraints=resolution.constraints,
            )
        state.missing_slots = []
        state.requested_slot = None
        state.error = None
        state.intent = str(resolution.domain).upper()
        state.domain = domain_from_legacy(state.intent, state.active_goal.candidate_facts)
        state.slots = {
            **project_domain_to_legacy_slots(state.domain),
            "user_text": state.slots.get("user_text"),
        }
