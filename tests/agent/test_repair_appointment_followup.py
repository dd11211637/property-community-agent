from types import SimpleNamespace
from uuid import uuid4

from property_agent.agent.application.domain_continuation import prepare_start_state
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import RepairWorkingState


def _completed_repair_state() -> GraphState:
    return GraphState(
        conversation_id="repair-appointment",
        actor_id=uuid4(),
        community_id=uuid4(),
        current_house_id=uuid4(),
        intent="REPAIR",
        domain=RepairWorkingState(
            action="create",
            description="客厅电灯坏了",
            location="客厅",
        ),
        slots={"action": "create", "description": "客厅电灯坏了", "location": "客厅"},
        tool_result={
            "ok": True,
            "tool": "repair_create",
            "data": {
                "work_order": {
                    "id": str(uuid4()),
                    "business_no": "WX-20260831-75C4658E",
                    "status": "PENDING_ACCEPTANCE",
                    "location": "客厅",
                }
            },
        },
    )


def test_time_followup_links_created_order_and_does_not_claim_appointment_success():
    previous = _completed_repair_state()
    context = SimpleNamespace(
        actor_id=previous.actor_id,
        community_id=previous.community_id,
        roles=("RESIDENT",),
    )

    prepared = prepare_start_state(
        conversation_id=previous.conversation_id,
        context=context,
        current_house_id=previous.current_house_id,
        previous=previous,
        user_text="今晚9点来修理",
        slots=None,
    )

    assert prepared.repair_followup_message is not None
    assert "WX-20260831-75C4658E" in prepared.repair_followup_message
    assert "还在等待维修人员接单" in prepared.repair_followup_message
    assert "不能直接确认" in prepared.repair_followup_message
    assert "预约成功" not in prepared.repair_followup_message
    assert prepared.state.slots["work_order_id"] == "WX-20260831-75C4658E"


def test_unrelated_followup_is_not_intercepted_as_an_appointment():
    previous = _completed_repair_state()
    context = SimpleNamespace(
        actor_id=previous.actor_id,
        community_id=previous.community_id,
        roles=("RESIDENT",),
    )

    prepared = prepare_start_state(
        conversation_id=previous.conversation_id,
        context=context,
        current_house_id=previous.current_house_id,
        previous=previous,
        user_text="谢谢",
        slots=None,
    )

    assert prepared.repair_followup_message is None
