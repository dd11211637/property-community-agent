"""Read-only tool registry backed by existing public application-service tools."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from property_agent.agent.read_contracts import ReadToolSpec
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import ok


def read_tool_specs() -> dict[str, ReadToolSpec]:
    return {
        "get_current_context": ReadToolSpec(
            "get_current_context", "获取当前认证社区与房屋显示范围", frozenset()
        ),
        "get_business_date": ReadToolSpec(
            "get_business_date", "获取 Asia/Shanghai 业务日期", frozenset()
        ),
        "search_announcements": ReadToolSpec(
            "search_announcements",
            "查询当前住户可见的已发布公告",
            frozenset({"topic", "target_date", "statuses", "limit"}),
        ),
        "search_community_knowledge": ReadToolSpec(
            "search_community_knowledge",
            "检索当前社区住户可见的已发布正式资料",
            frozenset({"query", "limit"}),
            frozenset({"query"}),
        ),
        "list_bills": ReadToolSpec(
            "list_bills", "查询当前房屋账单", frozenset({"period", "fee_type"})
        ),
        "get_bill": ReadToolSpec(
            "get_bill",
            "查询当前用户的一条账单",
            frozenset({"bill_id"}),
            frozenset({"bill_id"}),
        ),
        "list_work_orders": ReadToolSpec("list_work_orders", "查询当前房屋报修记录", frozenset()),
        "get_work_order": ReadToolSpec(
            "get_work_order",
            "查询当前用户可见的工单与时间线",
            frozenset({"work_order_id"}),
            frozenset({"work_order_id"}),
        ),
        "list_inspection_tasks": ReadToolSpec(
            "list_inspection_tasks",
            "查询当前用户有权查看的巡检任务并返回全量状态聚合",
            frozenset({"statuses", "assigned_to_me", "limit"}),
        ),
        "get_inspection_task": ReadToolSpec(
            "get_inspection_task",
            "查询一项巡检任务、时间线和当前可用动作",
            frozenset({"task_id"}),
            frozenset({"task_id"}),
        ),
        "list_security_events": ReadToolSpec(
            "list_security_events",
            "查询当前用户有权查看的安防事件",
            frozenset({"statuses", "risk_levels", "assigned_to_me", "limit"}),
        ),
        "get_security_event": ReadToolSpec(
            "get_security_event",
            "查询一项安防事件、时间线和当前可用动作",
            frozenset({"event_id"}),
            frozenset({"event_id"}),
        ),
    }


def build_read_tools(
    *,
    announcement_tools: dict[str, Any],
    billing_tools: dict[str, Any],
    repair_tools: dict[str, Any],
    inspection_tools: dict[str, Any],
) -> dict[str, Any]:
    def with_slots(tool, fixed: dict[str, Any] | None = None):
        def call(state: GraphState, arguments: dict[str, Any]):
            isolated = deepcopy(state)
            isolated.slots = {**isolated.slots, **(fixed or {}), **arguments}
            return tool(isolated)

        return call

    def current_context(state: GraphState, arguments: dict[str, Any]):
        del arguments
        return ok("get_current_context", **state.trusted_context)

    def business_date(state: GraphState, arguments: dict[str, Any]):
        del arguments
        value = (
            state.trusted_context.get("business_date")
            or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        )
        return ok("get_business_date", business_date=value, timezone="Asia/Shanghai")

    return {
        "get_current_context": current_context,
        "get_business_date": business_date,
        "search_announcements": with_slots(announcement_tools["announcement_list"]),
        "search_community_knowledge": with_slots(announcement_tools["community_knowledge_search"]),
        "list_bills": with_slots(billing_tools["billing_query"], {"query_type": "list"}),
        "get_bill": with_slots(billing_tools["billing_query"], {"query_type": "detail"}),
        "list_work_orders": with_slots(repair_tools["repair_list"]),
        "get_work_order": with_slots(repair_tools["repair_get"]),
        "list_inspection_tasks": with_slots(
            inspection_tools["inspection_list"], {"target": "task"}
        ),
        "get_inspection_task": with_slots(inspection_tools["inspection_get_task"]),
        "list_security_events": with_slots(
            inspection_tools["inspection_list"], {"target": "event"}
        ),
        "get_security_event": with_slots(inspection_tools["inspection_get_event"]),
    }
