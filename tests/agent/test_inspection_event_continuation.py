from types import SimpleNamespace
from uuid import uuid4

import pytest

from property_agent.agent.application.domain_continuation import prepare_start_state
from property_agent.agent.capabilities.adapters.inspection import SecurityEventCreateInput
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.specialists.inspection import InspectionSpecialist
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import EmptyWorkingState, InspectionEventWorkingState
from property_agent.platform.adapters.api.dependencies import RequestContext
from property_agent.platform.context import ExecutionSource


class FailingGateway:
    def propose_plan(self, *_args, **_kwargs):
        raise AssertionError("active task must not return to global planning")


def _previous_event(*, missing=("event_type",)) -> GraphState:
    house_id = uuid4()
    return GraphState(
        conversation_id="inspection-multiturn",
        actor_id=uuid4(),
        community_id=uuid4(),
        current_house_id=house_id,
        intent="INSPECTION",
        domain=InspectionEventWorkingState(
            action="report_event",
            location="安全出口",
            description="安全出口有人堆放杂物",
        ),
        slots={
            "action": "report_event",
            "target": "event",
            "location": "安全出口",
            "description": "安全出口有人堆放杂物",
        },
        missing_slots=list(missing),
        requested_slot=missing[0] if missing else None,
    )


def _context(previous: GraphState):
    return SimpleNamespace(
        actor_id=previous.actor_id,
        community_id=previous.community_id,
        roles=("SECURITY_GUARD",),
    )


def _runtime(previous: GraphState) -> RuntimeContext:
    request = RequestContext(
        actor_id=previous.actor_id,
        community_id=previous.community_id,
        roles=frozenset({"SECURITY_GUARD"}),
        bound_house_ids=frozenset({previous.current_house_id}),
        current_house_id=previous.current_house_id,
        request_id="continuation-test",
        execution_source=ExecutionSource.AGENT,
    )
    return RuntimeContext.from_request_context(
        request,
        conversation_id=previous.conversation_id,
    )


def test_first_turn_obstruction_enters_security_event_without_internal_enum_question():
    runtime_state = GraphState(
        conversation_id="inspection-first-turn",
        actor_id=uuid4(),
        community_id=uuid4(),
        current_house_id=uuid4(),
    )
    context = SimpleNamespace(
        actor_id=runtime_state.actor_id,
        community_id=runtime_state.community_id,
        roles=("SECURITY_GUARD",),
    )
    prepared = prepare_start_state(
        conversation_id=runtime_state.conversation_id,
        context=context,
        current_house_id=runtime_state.current_house_id,
        previous=None,
        user_text="安全出口有人堆放杂物",
        slots=None,
    )

    plan = SupervisorPlanner(FailingGateway()).create_plan(
        prepared.state,
        _runtime(runtime_state),
    )

    assert plan.steps[0].capability == "security_event_create"
    assert plan.steps[0].parameters["location"] == "安全出口"
    assert plan.steps[0].parameters["event_type"] == "EQUIPMENT_FAULT"


def test_capability_projection_uses_schema_defaults_instead_of_requesting_enum_values():
    state = _previous_event(missing=())
    step = SimpleNamespace(parameters={})

    projected = InspectionSpecialist.project_parameters(
        None,
        "security_event_create",
        step,
        state,
        (),
    )

    assert projected == {
        "location": "安全出口",
        "description": "安全出口有人堆放杂物",
    }
    validated = SecurityEventCreateInput.model_validate(projected)
    assert validated.event_type == "OTHER"
    assert validated.risk_level == "MEDIUM"


@pytest.mark.parametrize("reply", ["其他", "其他事件", "就是杂物"])
def test_short_reply_retains_security_event_capability(reply):
    previous = _previous_event()
    prepared = prepare_start_state(
        conversation_id=previous.conversation_id,
        context=_context(previous),
        current_house_id=previous.current_house_id,
        previous=previous,
        user_text=reply,
        slots=None,
    )

    plan = SupervisorPlanner(FailingGateway()).create_plan(prepared.state, _runtime(previous))

    assert prepared.state.intent == "INSPECTION"
    assert prepared.state.slots["action"] == "report_event"
    assert plan.steps[0].capability == "security_event_create"
    assert plan.steps[0].parameters["location"] == "安全出口"


def test_location_correction_updates_same_task():
    previous = _previous_event(missing=())
    prepared = prepare_start_state(
        conversation_id=previous.conversation_id,
        context=_context(previous),
        current_house_id=previous.current_house_id,
        previous=previous,
        user_text="不是安全出口，是地下车库出口",
        slots=None,
    )

    assert prepared.state.domain.kind == "inspection_event"
    assert prepared.state.slots["location"] == "地下车库出口"
    assert prepared.state.slots["action"] == "report_event"


def test_cancel_clears_transient_task_state_without_creating_event():
    previous = _previous_event()
    prepared = prepare_start_state(
        conversation_id=previous.conversation_id,
        context=_context(previous),
        current_house_id=previous.current_house_id,
        previous=previous,
        user_text="算了，不报了",
        slots=None,
    )

    assert isinstance(prepared.state.domain, EmptyWorkingState)
    assert prepared.state.intent is None
    assert prepared.immediate_message == "已取消本次安防事件上报，不会创建事件。"


def test_explicit_switch_allows_inspection_query_planning():
    previous = _previous_event()
    prepared = prepare_start_state(
        conversation_id=previous.conversation_id,
        context=_context(previous),
        current_house_id=previous.current_house_id,
        previous=previous,
        user_text="先不报了，看看今天还有哪些巡检任务",
        slots=None,
    )
    plan = SupervisorPlanner(FailingGateway()).create_plan(prepared.state, _runtime(previous))

    assert prepared.state.slots["action"] == "query"
    assert plan.steps[0].capability == "inspection_list"
