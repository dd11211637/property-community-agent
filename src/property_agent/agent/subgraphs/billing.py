"""账单子图 — PRD §6.5.3。

只读查询与"提交财务咨询"两条路径；AI 不参与任何金额或账单状态变更。
"""

from collections.abc import Mapping

from property_agent.agent.graph_core import StateGraph
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.base import attach_subgraph

NAME = "billing"


def select_billing_tool(state: GraphState) -> str:
    action = str(state.slots.get("action") or "").lower()
    if action in ("consult", "complain", "appeal"):
        return "billing_consult"
    if state.slots.get("subject") and state.slots.get("description"):
        return "billing_consult"
    return "billing_query"


def attach_billing_subgraph(graph: StateGraph, registry: Mapping) -> str:
    return attach_subgraph(
        graph, name=NAME, selector=select_billing_tool, registry=registry
    )
