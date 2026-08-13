"""报修子图 — PRD §6.5.3。"""

import re
from collections.abc import Mapping

from property_agent.agent.graph_core import StateGraph
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.base import attach_subgraph
from property_agent.repair.domain.classification import classify_repair_category

NAME = "repair"


def select_repair_tool(state: GraphState) -> str:
    text = str(state.slots.get("user_text") or "").strip()
    business_no = re.search(r"WX-\d{8}-[A-Z0-9]+", text, re.IGNORECASE)
    if business_no:
        state.slots["work_order_id"] = business_no.group(0).upper()

    action = str(state.slots.get("action") or "").lower()
    if action in ("create", "report", "new"):
        description = str(state.slots.get("description") or "").strip()
        if description:
            state.slots["category"] = classify_repair_category(description).value
        return "repair_create"
    if action in ("get", "detail") or state.slots.get("work_order_id"):
        return "repair_get"
    if action in ("list", "query"):
        return "repair_list"
    read_cues = ("查看", "查询", "记录", "进度", "工单详情")
    create_cues = (
        "我要报修",
        "需要报修",
        "申请报修",
        "报修",
        "漏电",
        "漏水",
        "坏了",
        "故障",
        "破损",
        "堵塞",
    )
    if any(cue in text for cue in read_cues):
        return "repair_list"
    if any(cue in text for cue in create_cues):
        # Persist the user's operation across slot-filling turns.  A later
        # answer such as "厨房" carries only a location and must not silently
        # fall back to the read-only list operation.
        state.slots["action"] = "create"
        description = str(state.slots.get("description") or "").strip()
        if description:
            state.slots["category"] = classify_repair_category(description).value
        generic_requests = {"我要报修", "需要报修", "申请报修", "报修", "我要保修"}
        if (
            not state.slots.get("description")
            and text not in generic_requests
            and state.slots.get("location")
        ):
            state.slots["description"] = text
            state.slots["category"] = classify_repair_category(text).value
        return "repair_create"
    # 未显式指定动作时：具备完整报修要素才视为下单，否则默认只读列表
    if state.slots.get("location") and state.slots.get("description"):
        state.slots["category"] = classify_repair_category(str(state.slots["description"])).value
        return "repair_create"
    return "repair_list"


def attach_repair_subgraph(graph: StateGraph, registry: Mapping) -> str:
    return attach_subgraph(graph, name=NAME, selector=select_repair_tool, registry=registry)
