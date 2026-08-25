from datetime import datetime, timedelta, timezone

import pytest

from property_agent.agent.orchestration import (
    GoalOutcome,
    ObjectiveClassification,
    OrchestrationBudget,
    Plan,
    PlanStep,
    PlanStepStatus,
    PlanValidator,
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.runtime import PreparedWrite
from property_agent.agent.state import AgentState


def _step(
    step_id: str,
    domain: str,
    specialist: SpecialistName,
    capability: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        domain=domain,
        specialist=specialist,
        goal=f"complete {domain} goal",
        dependencies=dependencies,
        capability=capability,
    )


def test_step_local_routing_allows_repair_then_billing_despite_global_repair_intent():
    plan = Plan(
        plan_id="plan-1",
        objective="查报修并查物业费",
        objective_classification=ObjectiveClassification.MULTI_DOMAIN,
        steps=(
            _step("repair", "repair", SpecialistName.REPAIR, "repair_list"),
            _step(
                "billing",
                "billing",
                SpecialistName.BILLING,
                "billing_query",
                dependencies=("repair",),
            ),
        ),
        current_step_id="repair",
    )

    accepted = PlanValidator().validate(plan, global_intent="REPAIR")

    assert accepted.steps[1].domain == "billing"


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (_step("bad", "billing", SpecialistName.REPAIR, "billing_query"), "specialist"),
        (_step("bad", "repair", SpecialistName.REPAIR, "billing_query"), "capability"),
    ],
)
def test_validator_rejects_domain_specialist_and_capability_mismatches(step, message):
    plan = Plan(
        plan_id="plan-1",
        objective="bad plan",
        objective_classification=ObjectiveClassification.SINGLE_DOMAIN,
        steps=(step,),
        current_step_id=step.step_id,
    )

    with pytest.raises(ValueError, match=message):
        PlanValidator().validate(plan)


def test_validator_rejects_cycles_and_oversized_plans():
    cyclic = Plan(
        plan_id="plan-cycle",
        objective="cycle",
        objective_classification=ObjectiveClassification.MULTI_DOMAIN,
        steps=(
            _step("a", "repair", SpecialistName.REPAIR, "repair_list", dependencies=("b",)),
            _step("b", "billing", SpecialistName.BILLING, "billing_query", dependencies=("a",)),
        ),
        current_step_id="a",
    )
    with pytest.raises(ValueError, match="cyclic"):
        PlanValidator().validate(cyclic)

    oversized = Plan(
        plan_id="plan-large",
        objective="large",
        objective_classification=ObjectiveClassification.MULTI_DOMAIN,
        steps=tuple(
            _step(str(index), "repair", SpecialistName.REPAIR, "repair_list") for index in range(9)
        ),
        current_step_id="0",
    )
    with pytest.raises(ValueError, match="maximum"):
        PlanValidator(max_steps=8).validate(oversized)


def test_restart_safe_budget_never_extends_original_deadline():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    original = OrchestrationBudget.start(now=now, duration=timedelta(minutes=5))

    resumed = original.resume(now=now + timedelta(minutes=2), server_ceiling=timedelta(minutes=1))

    assert resumed.started_at_utc == original.started_at_utc
    assert resumed.deadline_at_utc == now + timedelta(minutes=3)
    assert resumed.supervisor_steps == original.supervisor_steps


def test_prepared_write_requires_exact_capability_params_and_step_binding():
    prepared = PreparedWrite(
        confirmation_token="token",
        idempotency_key="key",
        approval_ref="approval",
        capability="repair_create",
        params_hash="hash-a",
        plan_id="plan-1",
        plan_step_id="write-a",
    )

    assert prepared.matches(
        capability="repair_create",
        params_hash="hash-a",
        plan_id="plan-1",
        plan_step_id="write-a",
    )
    assert not prepared.matches(
        capability="billing_consult",
        params_hash="hash-b",
        plan_id="plan-1",
        plan_step_id="write-b",
    )


def test_plan_step_status_is_typed_and_terminal_state_is_not_implicitly_completed():
    step = _step("repair", "repair", SpecialistName.REPAIR, "repair_list")
    assert step.status is PlanStepStatus.PENDING


def test_plan_budget_results_and_goal_outcomes_round_trip_in_checkpoint_state():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    step = _step("repair", "repair", SpecialistName.REPAIR, "repair_list")
    state = AgentState(
        conversation_id="conversation-1",
        plan=Plan(
            plan_id="plan-1",
            objective="查询报修",
            objective_classification=ObjectiveClassification.SINGLE_DOMAIN,
            steps=(step,),
            current_step_id=step.step_id,
        ),
        orchestration_budget=OrchestrationBudget.start(now=now, duration=timedelta(minutes=5)),
        specialist_results=(
            SpecialistResult(
                SpecialistOutcome.SUCCESS,
                step.step_id,
                SpecialistName.REPAIR,
                capability="repair_list",
                data={"count": 0},
            ),
        ),
        goal_outcomes={step.step_id: GoalOutcome.COMPLETED},
    )

    restored = AgentState.from_dict(state.to_dict())

    assert restored.plan == state.plan
    assert restored.orchestration_budget == state.orchestration_budget
    assert restored.specialist_results == state.specialist_results
    assert restored.goal_outcomes == state.goal_outcomes
