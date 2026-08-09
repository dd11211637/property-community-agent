"""统一智能体主路由图 — PRD §6.5.1 / §6.5.3。

拓扑：

    load_context → classify_intent → route ─┬─ repair.*      ─┐
                                            ├─ announcement.*├─ explain → finish
                                            ├─ billing.*     │
                                            ├─ inspection.*  ─┘
                                            └─ general_help  → finish

* ``load_context``：只从**可信** RequestContext 注入 actor / community / house，
  绝不接受用户自述身份（PRD §6.5.4）。
* ``classify_intent``：唯一允许调用模型的入口之一；模型不可用时降级为
  UNCERTAIN 并给出人工兜底提示（PRD R-02）。
* 子图内部完成"选工具 → 补槽位 → 确认 → 执行 / 转人工"。
"""

from collections.abc import Callable, Mapping
from typing import Any

from property_agent.agent.graph_core import Checkpointer, CompiledGraph, StateGraph
from property_agent.agent.model_gateway import ModelGateway
from property_agent.agent.nodes import classify_intent_node, explain_result_node
from property_agent.agent.policies import Intent
from property_agent.agent.routing import subgraph_entry
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs import (
    attach_announcement_subgraph,
    attach_billing_subgraph,
    attach_inspection_subgraph,
    attach_repair_subgraph,
)

GENERAL_HELP_TEXT = (
    "我可以帮您办理：报修、公告查询、账单查询与财务咨询、巡检与安防。请说明具体需求。"
)

ContextLoader = Callable[[GraphState], GraphState]


def _load_context_node(loader: ContextLoader | None):
    def node(state: GraphState) -> GraphState:
        return loader(state) if loader else state

    return node


def _route_node():
    def node(state: GraphState) -> GraphState:
        return state

    return node


def _router(state: GraphState) -> str:
    entry = subgraph_entry(state.intent)
    if entry is not None:
        return entry
    return "general_help"


def _general_help_node():
    def node(state: GraphState) -> GraphState:
        if state.intent == Intent.UNCERTAIN.value and state.messages:
            return state  # 分类节点已给出澄清提示，避免重复追问
        state.add_message("assistant", GENERAL_HELP_TEXT)
        return state

    return node


def build_agent_graph(
    *,
    gateway: ModelGateway,
    repair_tools: Mapping[str, Any],
    announcement_tools: Mapping[str, Any],
    billing_tools: Mapping[str, Any],
    inspection_tools: Mapping[str, Any],
    checkpointer: Checkpointer | None = None,
    context_loader: ContextLoader | None = None,
) -> CompiledGraph:
    graph = StateGraph()

    graph.add_node("load_context", _load_context_node(context_loader))
    graph.add_node("classify_intent", classify_intent_node(gateway))
    graph.add_node("route", _route_node())
    graph.add_node("general_help", _general_help_node())
    graph.add_node("explain", explain_result_node())
    graph.add_node("finish", lambda s: s)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "classify_intent")
    graph.add_edge("classify_intent", "route")
    graph.add_conditional_edges("route", _router)
    graph.add_edge("general_help", "finish")
    graph.add_edge("explain", "finish")
    graph.set_finish_point("finish")

    attach_repair_subgraph(graph, repair_tools)
    attach_announcement_subgraph(graph, announcement_tools)
    attach_billing_subgraph(graph, billing_tools)
    attach_inspection_subgraph(graph, inspection_tools)

    return graph.compile(checkpointer=checkpointer)
