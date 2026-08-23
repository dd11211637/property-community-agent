"""巡检与安防子图 — PRD §6.5.3。

关闭高风险安防事件属于写-高风险：路由到 handover，工具层也不会执行。
"""

from collections.abc import Mapping

from property_agent.agent.graph_core import StateGraph
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.base import attach_subgraph
from property_agent.agent.working_state import (
    EmptyWorkingState,
    InspectionEventWorkingState,
    InspectionTaskWorkingState,
    synchronize_typed_domain,
)

NAME = "inspection"


def select_inspection_tool(state: GraphState) -> str:
    if isinstance(state.domain, EmptyWorkingState) and state.intent is None:
        state.intent = "INSPECTION"
    if isinstance(state.domain, EmptyWorkingState) and state.intent == "INSPECTION":
        synchronize_typed_domain(state)
    domain = state.domain
    if not isinstance(domain, (InspectionTaskWorkingState, InspectionEventWorkingState)):
        return "inspection_list"
    action = str(domain.action or "").lower()
    # 兜底：action 缺失但文本含明确的写信号时，不落入只读列表。
    if not action:
        text = str(state.slots.get("user_text") or "")
        if any(marker in text for marker in ("上报", "报告")):
            return "security_event_create"
    if action in ("create", "plan", "new_task"):
        return "inspection_create"
    if action in ("start", "start_task"):
        return "inspection_start_task"
    if action in ("record", "add_record", "supplement"):
        return "inspection_add_record"
    if action in ("submit_record", "submit_records", "complete_records"):
        return "inspection_submit_records"
    if action in ("report_event", "create_event", "event_create"):
        return "security_event_create"
    if action in ("dispose_event", "submit_disposal"):
        return "security_event_submit_disposal"
    if action in ("ai_suggest", "suggest"):
        return "inspection_ai_suggest"
    if action in ("close_event", "close", "review_pass"):
        return "close_high_risk_event"
    return "inspection_list"


def attach_inspection_subgraph(graph: StateGraph, registry: Mapping) -> str:
    entry = attach_subgraph(graph, name=NAME, selector=select_inspection_tool, registry=registry)
    prepare = registry.get("__prepare_inspection__", lambda state: state)
    prepare_name = f"{NAME}.prepare"
    graph.add_node(prepare_name, prepare)
    graph.add_edge(prepare_name, entry)
    return prepare_name
