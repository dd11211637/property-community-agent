"""报修子图 — PRD §6.5.3。"""

from collections.abc import Mapping

from property_agent.agent.graph_core import StateGraph
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.base import attach_subgraph

NAME = "repair"


def select_repair_tool(state: GraphState) -> str:
    action = str(state.slots.get("action") or "").lower()
    if action in ("create", "report", "new"):
        return "repair_create"
    if action in ("get", "detail") or state.slots.get("work_order_id"):
        return "repair_get"
    if action in ("list", "query"):
        return "repair_list"
    # 未显式指定动作时：具备完整报修要素才视为下单，否则默认只读列表
    if state.slots.get("location") and state.slots.get("description"):
        return "repair_create"
    return "repair_list"


def attach_repair_subgraph(graph: StateGraph, registry: Mapping) -> str:
    return attach_subgraph(graph, name=NAME, selector=select_repair_tool, registry=registry)
