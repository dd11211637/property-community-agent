"""子图装配基座 — PRD §6.5.3。

四个业务子图共享同一套节点拓扑，差异只在"选哪个工具"和"用哪个工具注册表"：

    select_tool → collect_slots → confirm → (execute | handover | finish) → explain

子图以命名空间节点的形式挂进主图（``<模块>.<节点>``），因此中断/恢复、
检查点与主图完全一致——一次 interrupt 就能从任意子图内部恢复。

路由规则（确定性）：
* 必填槽位缺失          → 直接结束，只回追问，不触碰业务服务
* 写-高风险             → handover 节点，永不执行
* 用户取消（无 pending）→ 直接结束，不产生任何业务对象
* 读 / 已确认的写-低风险 → execute 节点
"""

from collections.abc import Callable, Mapping

from property_agent.agent.graph_core import StateGraph
from property_agent.agent.nodes import (
    collect_slots_node,
    confirm_action_node,
    execute_tool_node,
    handover_node,
    select_tool_node,
)
from property_agent.agent.policies import OperationLevel
from property_agent.agent.state import GraphState

ToolSelector = Callable[[GraphState], str]

EXPLAIN_NODE = "explain"
FINISH_NODE = "finish"


def _after_confirm(ns: Callable[[str], str]) -> Callable[[GraphState], str]:
    def router(state: GraphState) -> str:
        if state.handover_required:
            return ns("handover")
        if state.pending_action is None:
            return FINISH_NODE  # 用户取消
        if state.operation_level == OperationLevel.READ.value:
            return ns("execute")
        if (state._resume or {}).get("confirmed"):
            return ns("execute")
        return FINISH_NODE

    return router


def attach_subgraph(
    graph: StateGraph,
    *,
    name: str,
    selector: ToolSelector,
    registry: Mapping,
) -> str:
    """把一个模块子图挂到主图上，返回子图入口节点名。"""

    def ns(node: str) -> str:
        return f"{name}.{node}"

    graph.add_node(ns("select_tool"), select_tool_node(selector))
    graph.add_node(ns("collect_slots"), collect_slots_node())
    graph.add_node(ns("confirm"), confirm_action_node())
    graph.add_node(ns("execute"), execute_tool_node(registry))
    graph.add_node(ns("handover"), handover_node())

    graph.add_edge(ns("select_tool"), ns("collect_slots"))
    graph.add_conditional_edges(
        ns("collect_slots"),
        lambda s: FINISH_NODE if s.missing_slots else ns("confirm"),
    )
    graph.add_conditional_edges(ns("confirm"), _after_confirm(ns))
    graph.add_edge(ns("execute"), EXPLAIN_NODE)
    graph.add_edge(ns("handover"), EXPLAIN_NODE)

    return ns("select_tool")
