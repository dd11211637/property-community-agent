from property_agent.agent.slot_prompts import announcement_slot_prompt, inspection_slot_prompt
from property_agent.agent.state import AgentState


def test_announcement_prompt_collects_title_before_business_content():
    state = AgentState(conversation_id="conversation-1")
    state.intent = "ANNOUNCEMENT"
    state.requested_slot = "title"

    prompt = announcement_slot_prompt(state)

    assert prompt is not None
    assert prompt["field"] == "title"
    assert prompt["label"] == "公告标题"
    assert prompt["step"] == 1
    assert prompt["total_steps"] == 3


def test_announcement_prompt_preserves_completed_fields_and_empty_audience_scope():
    state = AgentState(conversation_id="conversation-1")
    state.intent = "ANNOUNCEMENT"
    state.requested_slot = "audience"
    state.slots.update(title="停水通知", body="今晚 22:00 至次日 06:00 停水")

    audience_prompt = announcement_slot_prompt(state)
    state.slots["audience"] = {}
    state.requested_slot = "body"
    completed = announcement_slot_prompt(state)

    assert audience_prompt is not None
    assert audience_prompt["step"] == 3
    assert audience_prompt["options"][0] == {"label": "全社区", "value": {}}
    assert completed is not None
    assert completed["completed"][-1]["value"] == "全社区"


def test_inspection_prompt_exposes_three_progressive_business_fields():
    state = AgentState(conversation_id="conversation-1")
    state.intent = "INSPECTION"
    state.requested_slot = "point"
    state.slots.update(
        action="create",
        title="每周小区安防巡检",
        description="检查消防设施和通道",
    )

    prompt = inspection_slot_prompt(state)

    assert prompt is not None
    assert prompt["field"] == "point"
    assert prompt["step"] == 3
    assert prompt["total_steps"] == 3
    assert prompt["options"][2] == {"label": "消防通道", "value": "消防通道"}
