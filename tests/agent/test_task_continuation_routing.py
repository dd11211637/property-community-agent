from uuid import uuid4

from property_agent.agent.application.task_continuation import (
    has_active_inspection_event,
    inspection_event_control,
)
from property_agent.agent.orchestration import ObjectiveClassification, Plan, PlanStatus
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import InspectionEventWorkingState


def _state(status=PlanStatus.NEEDS_CLARIFICATION):
    house_id = uuid4()
    return GraphState(
        conversation_id="task-guard",
        current_house_id=house_id,
        intent="INSPECTION",
        domain=InspectionEventWorkingState(action="report_event", location="地下车库"),
        plan=Plan(
            plan_id="plan-1",
            objective="上报安防事件",
            objective_classification=ObjectiveClassification.SINGLE_DOMAIN,
            steps=(),
            current_step_id=None,
            status=status,
        ),
    )


def test_needs_clarification_is_an_active_business_task():
    state = _state()

    assert has_active_inspection_event(state, state.current_house_id)


def test_completed_task_is_not_restored():
    state = _state(PlanStatus.COMPLETED)

    assert not has_active_inspection_event(state, state.current_house_id)


def test_task_control_requires_explicit_cancel_or_switch_signal():
    assert inspection_event_control("其他事件", next_action=None) is None
    assert inspection_event_control("算了，不报了", next_action=None) == "cancel"
    assert inspection_event_control("先不报了，看看巡检任务", next_action="query") == "switch"
