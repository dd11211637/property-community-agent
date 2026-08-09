"""6.5.d/6.5.e 子图与主路由图端到端测试。

覆盖 PRD 验收点：
* A-01 意图路由到正确子图，跨模块不串线
* A-02 必填槽位缺失时只追问，不调用业务服务
* A-03 写操作在用户确认前不落库；取消后不产生任何业务对象
* A-04 确认后携带确认令牌执行，且工具收到确定性幂等键
* S-03 / R-01 高风险动作不执行，转授权人工
* R-02 模型不可用时降级为澄清，不臆造意图
"""

import pytest

from property_agent.agent.graph import build_agent_graph
from property_agent.agent.graph_core import MemoryCheckpointer
from property_agent.agent.model_gateway import (
    DeterministicModelGateway,
    UnavailableModelGateway,
)
from property_agent.agent.policies import Intent
from property_agent.agent.routing import merge_registries, subgraph_entry
from property_agent.agent.state import GraphState


class _Recorder:
    """记录工具调用的假注册表，用于断言"谁被调用过"。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def tool(self, name: str, result: dict | None = None):
        def _call(state: GraphState) -> dict:
            self.calls.append((name, dict(state.slots)))
            return result or {"ok": True, "tool": name, "data": {"count": 0}}

        return _call

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def build_env(gateway=None, checkpointer=None):
    rec = _Recorder()
    repair = {
        "repair_list": rec.tool("repair_list"),
        "repair_get": rec.tool("repair_get"),
        "repair_create": rec.tool(
            "repair_create",
            {
                "ok": True,
                "tool": "repair_create",
                "data": {"work_order": {"id": "W-1", "status": "PENDING_ASSIGNMENT"}},
            },
        ),
    }
    announcement = {
        "announcement_list": rec.tool("announcement_list"),
        "announcement_get": rec.tool("announcement_get"),
        "announce_publish": rec.tool("announce_publish"),
    }
    billing = {
        "billing_query": rec.tool("billing_query"),
        "billing_consult": rec.tool("billing_consult"),
    }
    inspection = {
        "inspection_list": rec.tool("inspection_list"),
        "inspection_create": rec.tool("inspection_create"),
        "inspection_submit_record": rec.tool("inspection_submit_record"),
        "inspection_ai_suggest": rec.tool("inspection_ai_suggest"),
        "close_high_risk_event": rec.tool("close_high_risk_event"),
    }
    graph = build_agent_graph(
        gateway=gateway or DeterministicModelGateway(),
        repair_tools=repair,
        announcement_tools=announcement,
        billing_tools=billing,
        inspection_tools=inspection,
        checkpointer=checkpointer,
    )
    return graph, rec


def _state(conversation_id: str, text: str, **slots) -> GraphState:
    state = GraphState(conversation_id=conversation_id)
    state.slots["user_text"] = text
    state.slots.update(slots)
    return state


# ============================== A-01 路由 ==============================


def test_routing_table_matches_subgraph_entries():
    assert subgraph_entry(Intent.REPAIR.value) == "repair.select_tool"
    assert subgraph_entry(Intent.BILLING.value) == "billing.select_tool"
    assert subgraph_entry(Intent.UNCERTAIN.value) is None


def test_intent_routes_to_own_subgraph_only():
    graph, rec = build_env()

    graph.invoke(_state("c-r", "我家水管漏水想看看报修记录"))
    graph.invoke(_state("c-b", "这个月物业费怎么缴"))
    graph.invoke(_state("c-i", "今晚安保巡逻任务有哪些"))

    assert rec.names == ["repair_list", "billing_query", "inspection_list"]


def test_merge_registries_rejects_duplicates():
    with pytest.raises(ValueError):
        merge_registries({"a": 1}, {"a": 2})


# ============================== A-02 槽位追问 ==============================


def test_missing_slots_only_asks_never_calls_service():
    graph, rec = build_env()
    result = graph.invoke(_state("c1", "我要报修", action="create"))

    assert result["done"] is True
    assert set(result["state"].missing_slots) == {"category", "location", "description"}
    assert rec.calls == []  # 未触碰任何业务服务
    assert any("缺失" in m["content"] for m in result["state"].messages)


# ============================== A-03 确认前不写 ==============================


def test_write_pauses_before_any_service_call():
    cp = MemoryCheckpointer()
    graph, rec = build_env(checkpointer=cp)

    result = graph.invoke(
        _state(
            "c2",
            "报修",
            action="create",
            category="WATER_PLUMBING",
            location="厨房",
            description="水管漏水",
        )
    )

    assert result["done"] is False
    assert result["interrupt"]["type"] == "confirmation"
    assert result["interrupt"]["action"]["tool"] == "repair_create"
    assert rec.calls == []  # 中断发生在任何写调用之前
    assert "c2" in cp.list_threads()


def test_cancel_creates_no_object():
    cp = MemoryCheckpointer()
    graph, rec = build_env(checkpointer=cp)
    graph.invoke(
        _state(
            "c3",
            "报修",
            action="create",
            category="ELECTRICAL",
            location="客厅",
            description="插座没电",
        )
    )

    resumed = graph.resume("c3", {"confirmed": False})

    assert resumed["done"] is True
    assert rec.calls == []
    assert resumed["state"].tool_result is None
    assert any("已取消" in m["content"] for m in resumed["state"].messages)


# ============================== A-04 确认后执行 ==============================


def test_confirm_executes_with_token():
    cp = MemoryCheckpointer()
    graph, rec = build_env(checkpointer=cp)
    graph.invoke(
        _state(
            "c4",
            "报修",
            action="create",
            category="WATER_PLUMBING",
            location="厨房",
            description="水管漏水",
        )
    )

    resumed = graph.resume("c4", {"confirmed": True, "confirmation_token": "tok-123"})

    assert resumed["done"] is True
    assert rec.names == ["repair_create"]
    assert resumed["state"].confirmation_token == "tok-123"
    assert resumed["state"].tool_result["data"]["work_order"]["id"] == "W-1"
    assert any("已完成" in m["content"] for m in resumed["state"].messages)
    # 本轮结束后恢复态被清理，下一轮不会被误判为已确认
    assert resumed["state"]._resume is None
    assert resumed["state"]._interrupt_node is None


# ============================== S-03 / R-01 高风险转人工 ==============================


def test_high_risk_publish_never_executes():
    graph, rec = build_env()
    result = graph.invoke(_state("c5", "发一条停水公告", action="publish", announcement_id="A-1"))

    assert result["done"] is True
    assert rec.calls == []  # 高风险动作在图内被拦截，未进入执行节点
    assert result["state"].handover_required is True
    assert any("转人工" in m["content"] for m in result["state"].messages)


def test_high_risk_close_event_never_executes():
    graph, rec = build_env()
    result = graph.invoke(_state("c6", "关闭这个安防事件", action="close_event", event_id="E-1"))

    assert rec.calls == []
    assert result["state"].handover_required is True


# ============================== R-02 模型降级 ==============================


def test_model_unavailable_degrades_to_clarification():
    graph, rec = build_env(gateway=UnavailableModelGateway())
    result = graph.invoke(_state("c7", "我家水管漏水"))

    assert result["state"].intent == Intent.UNCERTAIN.value
    assert rec.calls == []
    assert any("不可用" in m["content"] for m in result["state"].messages)


# ============================== 读操作直通 ==============================


def test_read_path_needs_no_confirmation():
    graph, rec = build_env()
    result = graph.invoke(_state("c8", "查一下我的账单", query_type="list"))

    assert result["done"] is True
    assert rec.names == ["billing_query"]
    assert result["state"].confirmation_token is None
