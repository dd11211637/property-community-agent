from dataclasses import replace
from types import SimpleNamespace

import pytest

from property_agent.agent.orchestration import (
    ExecutionMode,
    ObjectiveClassification,
    Plan,
    PlanStatus,
    PlanStep,
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.react_contracts import GoalStatus, ReactDecision, ReactDecisionType
from property_agent.agent.react_runtime import ReactCoordinator
from property_agent.agent.runtime import ExecutionPolicy
from property_agent.agent.state import AgentState


class DecisionGateway:
    def __init__(self, *decisions):
        self.decisions = list(decisions)

    def react_decide(self, context):
        assert context["allowed_capabilities"]
        return self.decisions.pop(0)


class FakeSpecialist:
    def __init__(self, result_factory):
        self.result_factory = result_factory
        self.calls = []

    def invoke(self, step, state, runtime, prior_results):
        self.calls.append((step.capability, step.parameters))
        return self.result_factory(step)


def _state(domain="repair", specialist=SpecialistName.REPAIR):
    step = PlanStep("g1", domain, specialist, "complete the goal", parameters={"location": "1A"})
    return AgentState(
        "conversation-1",
        plan=Plan(
            "p1",
            "goal",
            ObjectiveClassification.SINGLE_DOMAIN,
            (step,),
            "g1",
            execution_mode=ExecutionMode.REACT,
        ),
    )


def _runtime(**changes):
    policy = replace(ExecutionPolicy(react_domains=frozenset({"repair", "inspection"})), **changes)
    return SimpleNamespace(execution_policy=policy)


def _success(step):
    return SpecialistResult(
        SpecialistOutcome.SUCCESS,
        step.step_id,
        step.specialist,
        capability=step.capability,
        data={"count": 0, "query_location": "1A"},
        fingerprint="result-1",
    )


def test_react_act_observe_finish_loop():
    decisions = (
        ReactDecision(
            ReactDecisionType.ACT,
            GoalStatus.IN_PROGRESS,
            capability="repair_list",
            arguments={"location": "1A"},
            reason_code="LOOKUP",
        ),
        ReactDecision(
            ReactDecisionType.FINISH,
            GoalStatus.COMPLETED,
            reason_code="VERIFIED_EMPTY",
        ),
    )
    specialist = FakeSpecialist(_success)
    coordinator = ReactCoordinator(DecisionGateway(*decisions), {SpecialistName.REPAIR: specialist})
    state = _state()
    coordinator.reason(state, _runtime())
    coordinator.action(state, _runtime())
    assert state.active_goal.action_count == 1
    assert state.active_goal.observations[0].capability == "repair_list"
    coordinator.reason(state, _runtime())
    assert state.goal_outcomes["g1"].value == "completed"
    assert state.active_goal.status is GoalStatus.COMPLETED
    assert specialist.calls == [("repair_list", {"location": "1A"})]


def test_react_decision_rejects_authority_and_invalid_shapes():
    with pytest.raises(ValueError, match="server-owned"):
        ReactDecision(
            ReactDecisionType.ACT,
            GoalStatus.IN_PROGRESS,
            capability="repair_list",
            arguments={"house_id": "forged"},
        )
    with pytest.raises(ValueError, match="CLARIFY"):
        ReactDecision(ReactDecisionType.CLARIFY, GoalStatus.NEEDS_CLARIFICATION)


def test_provider_failure_falls_back_once_to_legacy_plan():
    class BrokenGateway:
        def react_decide(self, context):
            raise TimeoutError

    state = _state()
    state.legacy_plan = replace(
        state.plan,
        steps=(replace(state.plan.steps[0], capability="repair_list"),),
        execution_mode=ExecutionMode.LEGACY,
    )
    coordinator = ReactCoordinator(BrokenGateway(), {})
    coordinator.reason(state, _runtime())
    assert state.plan.execution_mode is ExecutionMode.LEGACY
    assert state.active_goal.degraded and state.active_goal.fallback_used


def test_checkpoint_v3_and_v2_legacy_compatibility():
    state = _state()
    ReactCoordinator(DecisionGateway(), {}).ensure_goal(state)
    encoded = state.to_dict()
    assert encoded["schema_version"] == 3
    restored = AgentState.from_dict(encoded)
    assert restored.active_goal.goal_id == "g1"
    legacy = dict(encoded)
    legacy["schema_version"] = 2
    legacy.pop("active_goal")
    legacy.pop("legacy_plan")
    legacy["plan"] = {**legacy["plan"], "execution_mode": "legacy"}
    restored_v2 = AgentState.from_dict(legacy)
    assert restored_v2.schema_version == 3
    assert restored_v2.active_goal is None


def test_clarification_continuation_keeps_same_goal():
    state = _state()
    coordinator = ReactCoordinator(DecisionGateway(), {})
    goal = coordinator.ensure_goal(state)
    goal.status = GoalStatus.NEEDS_CLARIFICATION
    goal.missing_information = ("location",)
    state.plan = replace(state.plan, status=PlanStatus.NEEDS_CLARIFICATION)
    state.slots["location"] = "2B"
    state._continuation = True
    coordinator.resume_clarification(state)
    assert state.active_goal is goal
    assert goal.candidate_facts["location"] == "2B"
    assert goal.status is GoalStatus.IN_PROGRESS
