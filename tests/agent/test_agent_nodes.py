"""6.5.b 节点层测试：意图/槽位/确认(中断前不写)/执行/解释/接管。"""

from property_agent.agent.model_gateway import DeterministicModelGateway, UnavailableModelGateway
from property_agent.agent.nodes import (
    classify_intent_node,
    collect_slots_node,
    confirm_action_node,
    execute_tool_node,
    explain_result_node,
    handover_node,
)
from property_agent.agent.policies import Intent, OperationLevel
from property_agent.agent.state import GraphState


def _state(**kw) -> GraphState:
    return GraphState(conversation_id="c1", **kw)


def test_classify_intent_sets_intent():
    node = classify_intent_node(DeterministicModelGateway())
    s = _state(slots={"user_text": "水管漏水要报修"})
    node(s)
    assert s.intent == Intent.REPAIR.value
    assert s.confidence > 0.5


def test_classify_intent_low_confidence_becomes_uncertain():
    node = classify_intent_node(DeterministicModelGateway())
    s = _state(slots={"user_text": "你好呀"})
    node(s)
    assert s.intent == Intent.UNCERTAIN.value


def test_classify_intent_model_unavailable_degrades():
    node = classify_intent_node(UnavailableModelGateway())
    s = _state(slots={"user_text": "漏水报修"})
    node(s)
    assert s.intent == Intent.UNCERTAIN.value
    assert any("不可用" in m["content"] for m in s.messages)


def test_collect_slots_reports_missing():
    node = collect_slots_node()
    s = _state(intent=Intent.REPAIR.value, slots={})
    node(s)
    assert set(s.missing_slots) == {"category", "location", "description"}
    assert any("缺失" in m["content"] for m in s.messages)


def test_confirm_read_level_no_interrupt():
    node = confirm_action_node()
    s = _state(intent=Intent.REPAIR.value, slots={"tool": "repair_get", "house_id": "x"})
    # read 工具名 -> 等级 read，不中断
    s.slots["tool"] = "repair_list"
    node(s)
    assert s.operation_level == OperationLevel.READ.value
    assert s.pending_action is not None


def test_confirm_write_low_risk_interrupts_before_write():
    node = confirm_action_node()
    slots = {"tool": "repair_create", "category": "x", "location": "y", "description": "z"}
    s = _state(intent=Intent.REPAIR.value, slots=slots)

    # 直接调用会触发 interrupt（以异常形式抛出）
    try:
        node(s)
    except Exception as exc:
        assert exc.payload["type"] == "confirmation"
    # 中断前不得有任何业务写结果（PRD A-03）
    assert s.tool_result is None


def test_confirm_write_high_risk_marks_handover():
    node = confirm_action_node()
    s = _state(
        intent=Intent.ANNOUNCEMENT.value,
        slots={"tool": "announce_publish", "title": "t", "body": "b", "audience": "all"},
    )
    node(s)
    assert s.operation_level == OperationLevel.WRITE_HIGH_RISK.value
    assert s.handover_required is True


def test_execute_tool_runs_and_captures_error():
    calls = {}

    def ok(state):
        calls["ran"] = True
        return {"summary": "done"}

    def boom(state):
        raise ValueError("boom")

    node_ok = execute_tool_node({"repair_create": ok})
    s = _state(pending_action={"tool": "repair_create"})
    node_ok(s)
    assert s.tool_result == {"summary": "done"}
    assert not s.error

    node_boom = execute_tool_node({"repair_create": boom})
    s2 = _state(pending_action={"tool": "repair_create"})
    node_boom(s2)
    assert s2.error == "boom"
    assert s2.retry_count == 1


def test_handover_marks_required():
    node = handover_node()
    s = _state()
    node(s)
    assert s.handover_required is True
    assert any("高风险" in m["content"] for m in s.messages)


def test_explain_shows_error_and_success():
    ok = explain_result_node()
    s = _state(intent=Intent.REPAIR.value, tool_result={"summary": "工单已建"})
    ok(s)
    assert any("工单已建" in m["content"] for m in s.messages)

    err = explain_result_node()
    s2 = _state(intent=Intent.REPAIR.value, error="boom")
    err(s2)
    assert any("未能完成" in m["content"] for m in s2.messages)
