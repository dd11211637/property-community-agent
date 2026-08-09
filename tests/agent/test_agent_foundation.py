"""6.5.a 基础内核单元测试：policies / model_gateway / graph_core。"""

import pytest

from property_agent.agent.graph_core import (
    MemoryCheckpointer,
    StateGraph,
    interrupt,
)
from property_agent.agent.model_gateway import (
    DeterministicModelGateway,
    UnavailableModelGateway,
)
from property_agent.agent.policies import (
    Intent,
    OperationLevel,
    classify_operation_level,
    missing_slots_for,
)
from property_agent.agent.state import GraphState


# ============================== policies ==============================
def test_classify_operation_level():
    assert classify_operation_level("REPAIR") == OperationLevel.WRITE_LOW_RISK.value
    assert classify_operation_level("BILLING") == OperationLevel.WRITE_LOW_RISK.value
    assert classify_operation_level("INSPECTION") == OperationLevel.WRITE_LOW_RISK.value
    assert classify_operation_level("GENERAL_HELP") == OperationLevel.READ.value
    # 高风险工具名强制升级为写-高风险（转人工接管）
    assert (
        classify_operation_level("ANNOUNCEMENT", tool_name="announce_publish")
        == OperationLevel.WRITE_HIGH_RISK.value
    )
    assert (
        classify_operation_level("INSPECTION", tool_name="close_high_risk_event")
        == OperationLevel.WRITE_HIGH_RISK.value
    )


def test_missing_slots_for():
    assert missing_slots_for("REPAIR", {}) == ["category", "location", "description"]
    assert missing_slots_for("REPAIR", {"category": "x", "location": "y"}) == ["description"]
    assert missing_slots_for("REPAIR", {"category": "x", "location": "y", "description": "z"}) == []


# ============================== model gateway ==============================
def test_deterministic_gateway_classifies():
    gw = DeterministicModelGateway()
    assert gw.ready() is True
    intent, conf = gw.classify_intent("我家水管漏水了，需要报修")
    assert intent == Intent.REPAIR.value
    assert conf > 0.5
    assert gw.classify_intent("这个月物业费怎么缴")[0] == Intent.BILLING.value
    assert gw.classify_intent("小区要发停水公告")[0] == Intent.ANNOUNCEMENT.value
    assert gw.classify_intent("今晚安保巡逻有隐患")[0] == Intent.INSPECTION.value
    assert gw.classify_intent("帮助")[0] == Intent.GENERAL_HELP.value
    assert gw.classify_intent("现在社区服务守则是怎么样")[0] == Intent.GENERAL_HELP.value
    assert gw.classify_intent("随便聊聊")[0] == Intent.UNCERTAIN.value


def test_unavailable_gateway_degrades():
    gw = UnavailableModelGateway()
    assert gw.ready() is False
    with pytest.raises(RuntimeError):
        gw.classify_intent("anything")


# ============================== graph core: interrupt / resume ==============================
def _demo_graph() -> StateGraph:
    g = StateGraph()

    def entry(s: GraphState) -> GraphState:
        s.add_message("system", "start")
        return s

    def confirm(s: GraphState) -> GraphState:
        if s._resume is not None:
            if s._resume.get("confirmed"):
                s.add_message("assistant", "executing")
            else:
                s.add_message("assistant", "cancelled")
            return s
        s.pending_action = {"op": "demo"}
        interrupt({"type": "confirm", "action": "demo"})  # 暂停，且不调用任何写操作

    def execute(s: GraphState) -> GraphState:
        s.tool_result = {"ok": True}
        return s

    g.add_node("entry", entry)
    g.add_node("confirm", confirm)
    g.add_node("execute", execute)
    g.add_node("finish", lambda s: s)
    g.set_entry_point("entry")
    g.add_edge("entry", "confirm")
    g.add_conditional_edges(
        "confirm",
        lambda s: "execute" if (s._resume or {}).get("confirmed") else "finish",
    )
    g.set_finish_point("finish")
    return g


def test_invoke_then_confirm_executes():
    cp = MemoryCheckpointer()
    graph = _demo_graph().compile(checkpointer=cp)
    result = graph.invoke(GraphState(conversation_id="t1"))
    # 写操作前未确认 -> 不应执行，且返回 interrupt 供外部确认
    assert result["done"] is False
    assert result["interrupt"]["type"] == "confirm"
    assert result["state"].tool_result is None
    assert "t1" in cp.list_threads()

    resumed = graph.resume("t1", {"confirmed": True})
    assert resumed["done"] is True
    assert resumed["state"].tool_result == {"ok": True}
    assert any("executing" in m["content"] for m in resumed["state"].messages)


def test_invoke_then_cancel_does_not_execute():
    cp = MemoryCheckpointer()
    graph = _demo_graph().compile(checkpointer=cp)
    graph.invoke(GraphState(conversation_id="t2"))
    resumed = graph.resume("t2", {"confirmed": False})
    assert resumed["done"] is True
    assert resumed["state"].tool_result is None  # 取消不产生任何业务对象
    assert any("cancelled" in m["content"] for m in resumed["state"].messages)


def test_resume_without_checkpointer_fails():
    graph = _demo_graph().compile(checkpointer=None)
    with pytest.raises(RuntimeError):
        graph.resume("nope", {"confirmed": True})
