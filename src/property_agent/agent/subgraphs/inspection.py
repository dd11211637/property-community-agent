"""巡检与安防子图 — PRD §6.5.3。

关闭高风险安防事件属于写-高风险：路由到 handover，工具层也不会执行。
"""

from collections.abc import Mapping

from property_agent.agent.graph_core import StateGraph
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.base import attach_subgraph

NAME = "inspection"


def select_inspection_tool(state: GraphState) -> str:
    action = str(state.slots.get("action") or "").lower()
    if action in ("create", "plan", "new_task"):
        return "inspection_create"
    if action in ("record", "submit_record", "supplement"):
        return "inspection_submit_record"
    if action in ("ai_suggest", "suggest"):
        return "inspection_ai_suggest"
    if action in ("close_event", "close", "review_pass"):
        return "close_high_risk_event"
    return "inspection_list"


def attach_inspection_subgraph(graph: StateGraph, registry: Mapping) -> str:
    return attach_subgraph(graph, name=NAME, selector=select_inspection_tool, registry=registry)
