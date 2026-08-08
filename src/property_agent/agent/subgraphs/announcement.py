"""公告子图 — PRD §6.5.3。

发布属于写-高风险：子图会把它路由到 handover，工具层也不会执行发布。
"""

from collections.abc import Mapping

from property_agent.agent.graph_core import StateGraph
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.base import attach_subgraph

NAME = "announcement"


def select_announcement_tool(state: GraphState) -> str:
    action = str(state.slots.get("action") or "").lower()
    if action in ("publish", "release", "send"):
        return "announce_publish"
    if action in ("get", "detail") or state.slots.get("announcement_id"):
        return "announcement_get"
    return "announcement_list"


def attach_announcement_subgraph(graph: StateGraph, registry: Mapping) -> str:
    return attach_subgraph(
        graph, name=NAME, selector=select_announcement_tool, registry=registry
    )
