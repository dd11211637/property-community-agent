from dataclasses import replace

from property_agent.agent.orchestration import (
    PlanStatus,
    PlanStepStatus,
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.react_contracts import (
    ActiveGoalState,
    GoalStatus,
    ReactDecision,
    ReactDecisionType,
    ReactObservation,
)
from property_agent.agent.react_runtime import ReactCoordinator
from property_agent.platform.application.hashing import canonical_hash
from tests.agent.test_react_runtime import DecisionGateway, FakeSpecialist, _runtime, _state


def _act(capability, arguments):
    return ReactDecision(
        ReactDecisionType.ACT,
        GoalStatus.IN_PROGRESS,
        capability=capability,
        arguments=arguments,
        reason_code="NEXT",
    )


def test_specialist_allowlist_rejects_cross_domain_action_without_calling_executor():
    specialist = FakeSpecialist(lambda step: None)
    state = _state()
    coordinator = ReactCoordinator(
        DecisionGateway(_act("billing_query", {"query_type": "list"})),
        {SpecialistName.REPAIR: specialist},
    )
    coordinator.reason(state, _runtime())
    coordinator.action(state, _runtime())
    assert not specialist.calls
    assert state.active_goal.observations[-1].error_code == "CAPABILITY_NOT_IN_SPECIALIST_ALLOWLIST"


def test_duplicate_action_guard_hands_over_after_two_identical_observations():
    decision = _act("repair_list", {"location": "1A"})
    state = _state()
    coordinator = ReactCoordinator(DecisionGateway(decision), {})
    goal = coordinator.ensure_goal(state)
    fingerprint = canonical_hash(decision.arguments)
    observation = ReactObservation("repair_list", True, fingerprint, "same", data={})
    goal.observations = (observation, observation)
    coordinator.reason(state, _runtime())
    coordinator.action(state, _runtime())
    assert state.handover_required
    assert state.error == "REACT_NO_PROGRESS"


def test_unpreauthorized_domain_transition_requires_clarification():
    decision = ReactDecision(
        ReactDecisionType.FINISH,
        GoalStatus.PARTIAL,
        reason_code="NEXT_DOMAIN",
        requested_domain="billing",
    )
    state = _state()
    coordinator = ReactCoordinator(DecisionGateway(decision), {})
    coordinator.reason(state, _runtime())
    assert state.plan.status.value == "needs-clarification"
    assert state.requested_slot == "requested_domain_authorization"


def test_repair_create_requires_same_goal_preread():
    state = _state()
    specialist = FakeSpecialist(lambda step: None)
    decision = _act(
        "repair_create",
        {"location": "1A", "description": "leak", "appointment_at": None},
    )
    coordinator = ReactCoordinator(DecisionGateway(decision), {SpecialistName.REPAIR: specialist})
    coordinator.reason(state, _runtime())
    coordinator.action(state, _runtime())
    assert not specialist.calls
    assert state.active_goal.observations[-1].error_code == "REPAIR_PREREAD_REQUIRED"


def test_active_repair_observation_prevents_duplicate_create():
    state = _state()
    specialist = FakeSpecialist(lambda step: None)
    decision = _act("repair_create", {"location": "1A", "description": "leak"})
    coordinator = ReactCoordinator(DecisionGateway(decision), {SpecialistName.REPAIR: specialist})
    goal = coordinator.ensure_goal(state)
    goal.append_observation(
        ReactObservation(
            "repair_list",
            True,
            canonical_hash({"location": "1A"}),
            "list-1",
            data={
                "query_location": "1A",
                "items": [{"id": "existing", "status": "PENDING", "location": "1A"}],
            },
        )
    )
    coordinator.reason(state, _runtime())
    coordinator.action(state, _runtime())
    assert not specialist.calls
    assert goal.observations[-1].error_code == "ACTIVE_REPAIR_EXISTS"


def test_hitl_resume_reuses_exact_capability_and_parameters_once():
    state = _state()
    decision = _act("repair_create", {"location": "1A", "description": "leak"})
    calls = []

    def result(step):
        calls.append((step.capability, step.parameters))
        if len(calls) == 1:
            return SpecialistResult(
                SpecialistOutcome.HITL_REQUIRED,
                step.step_id,
                step.specialist,
                capability=step.capability,
                data={
                    "parameters": step.parameters,
                    "params_hash": canonical_hash(step.parameters),
                    "operation_level": "WRITE_HIGH",
                },
            )
        return SpecialistResult(
            SpecialistOutcome.SUCCESS,
            step.step_id,
            step.specialist,
            capability=step.capability,
            data={"work_order": {"id": "new"}},
            fingerprint="created",
        )

    specialist = FakeSpecialist(result)
    coordinator = ReactCoordinator(DecisionGateway(decision), {SpecialistName.REPAIR: specialist})
    goal = coordinator.ensure_goal(state)
    goal.append_observation(
        ReactObservation(
            "repair_list",
            True,
            canonical_hash({"location": "1A"}),
            "empty",
            data={"query_location": "1A", "items": []},
        )
    )
    coordinator.reason(state, _runtime())
    coordinator.action(state, _runtime())
    assert state.pending_action["goal_id"] == "g1"
    state.plan = state.plan.replace_step(
        replace(state.plan.steps[0], status=PlanStepStatus.PENDING)
    )
    state.plan = replace(state.plan, status=PlanStatus.ACTIVE)
    goal.status = GoalStatus.IN_PROGRESS
    coordinator.action(state, _runtime())
    assert calls[0] == calls[1]
    assert goal.action_count == 1


def test_billing_consult_requires_rule_absence_observation():
    state = _state("billing", SpecialistName.BILLING)
    goal = ActiveGoalState("g1", "billing", "billing")
    state.active_goal = goal
    decision = _act("billing_consult", {"subject": "rule", "description": "unknown"})
    specialist = FakeSpecialist(
        lambda step: SpecialistResult(
            SpecialistOutcome.SUCCESS,
            step.step_id,
            step.specialist,
            capability=step.capability,
            data={"consultation": {"id": "1"}},
        )
    )
    coordinator = ReactCoordinator(DecisionGateway(decision), {SpecialistName.BILLING: specialist})
    coordinator.reason(state, _runtime())
    coordinator.action(state, _runtime())
    assert not specialist.calls
    assert goal.observations[-1].error_code == "BILLING_RULE_NOT_PROVEN_MISSING"
