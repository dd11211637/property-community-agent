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


def test_deterministic_inspection_actions_are_structured():
    gateway = DeterministicModelGateway()
    assert gateway.analyze("巡检任务都完成了吗").slots == {"action": "query", "target": "task"}
    create = gateway.analyze("我要对1栋1单元所有消防设施进行巡检")
    assert create.slots["action"] == "create"
    assert create.slots["target"] == "task"
    assert create.slots["title"] == "消防设施巡检"
    assert create.slots["description"] == "对1栋1单元所有消防设施进行巡检"
    assert create.slots["point"] == "1栋1单元"
    assert gateway.analyze("1栋消防设施巡检完成了吗").slots == {
        "action": "query",
        "target": "task",
    }
    report = gateway.analyze("1栋厨房闻到强烈燃气味，请上报事件")
    assert report.slots["action"] == "report_event"
    assert report.slots["event_type"] == "GAS_LEAK"
    assert report.slots["risk_level"] == "HIGH_RISK"
    assert report.slots["location"] == "1栋厨房"


def test_classify_intent_low_confidence_becomes_uncertain():
    node = classify_intent_node(DeterministicModelGateway())
    s = _state(slots={"user_text": "随便聊聊"})
    node(s)
    assert s.intent == Intent.UNCERTAIN.value


def test_contextual_followup_can_replace_a_previous_period():
    node = classify_intent_node(
        DeterministicModelGateway(today_provider=lambda: __import__("datetime").date(2026, 8, 12))
    )
    s = _state(
        intent=Intent.BILLING.value,
        slots={"user_text": "那上个月呢", "period": "2026-08", "query_type": "list"},
        messages=[
            {"role": "assistant", "content": "已找到本月账单"},
            {"role": "user", "content": "那上个月呢"},
        ],
        _continuation=True,
        _contextual_followup=True,
    )

    node(s)

    assert s.intent == Intent.BILLING.value
    assert s.slots["period"] == "2026-07"


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
    assert s.missing_slots == ["description", "location"]
    assert s.requested_slot == "description"
    assert s.messages[-1]["content"] == "请描述一下具体出现了什么故障？"
    assert s.messages[-1]["field"] == "description"
    assert "缺失" not in s.messages[-1]["content"]


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


def test_confirm_announcement_publish_pauses_for_human_confirmation():
    node = confirm_action_node()
    s = _state(
        intent=Intent.ANNOUNCEMENT.value,
        slots={"tool": "announce_publish", "title": "t", "body": "b", "audience": "all"},
    )
    try:
        node(s)
    except Exception as exc:
        assert exc.payload["type"] == "confirmation"
    assert s.operation_level == OperationLevel.WRITE_LOW_RISK.value
    assert s.handover_required is False
    assert s.pending_action["tool"] == "announce_publish"


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
    s = _state(
        intent=Intent.REPAIR.value,
        tool_result={
            "ok": True,
            "tool": "repair_create",
            "data": {
                "work_order": {
                    "business_no": "WX-001",
                    "status": "PENDING_ASSIGNMENT",
                }
            },
        },
    )
    ok(s)
    reply = s.messages[-1]["content"]
    assert reply == "报修已提交，工单号 WX-001，当前等待物业派单。"
    assert "REPAIR" not in reply
    assert "PENDING_ASSIGNMENT" not in reply
    assert "work_order" not in reply

    err = explain_result_node()
    s2 = _state(intent=Intent.REPAIR.value, error="boom")
    err(s2)
    assert any("暂时未能完成" in m["content"] for m in s2.messages)


def test_explain_inspection_write_actions_in_business_language():
    cases = [
        ("inspection_start_task", "IN_PROGRESS", "已开始巡检", "巡检中"),
        ("inspection_add_record", "IN_PROGRESS", "巡检记录已追加", "巡检中"),
        (
            "inspection_submit_records",
            "SUBMITTED",
            "最终巡检记录已提交",
            "等待管理者复核",
        ),
    ]
    for tool, status, action_text, status_text in cases:
        state = _state(
            intent=Intent.INSPECTION.value,
            tool_result={
                "ok": True,
                "tool": tool,
                "data": {"task": {"business_no": "IT-001", "status": status}},
            },
        )
        explain_result_node()(state)
        reply = state.messages[-1]["content"]
        assert action_text in reply
        assert status_text in reply
        assert status not in reply
