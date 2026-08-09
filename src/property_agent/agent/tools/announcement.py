"""公告工具 — 只调用 ``AnnouncementService`` 公开方法（PRD §6.2 / §6.5.7）。

- ``announcement_list`` / ``announcement_get``：只读
- ``announce_publish``：**写-高风险**。工具不执行发布，只返回转授权人工的
  接管指令（PRD §6.5.7 高风险动作智能体不得直接落库）。草稿由人工在业务
  端继续处理，AI 侧到此为止。
"""

from typing import Any
from uuid import UUID

from property_agent.agent.policies import OperationLevel
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import (
    ContextProvider,
    Tool,
    assert_level,
    handover,
    ok,
    require_slot,
)
from property_agent.announcement.application.commands import AnnouncementSearch


def _brief(announcement: Any) -> dict[str, Any]:
    return {
        "id": str(announcement.id),
        "title": getattr(announcement, "title", None),
        "category": str(getattr(announcement, "category", "")),
        "status": str(getattr(announcement, "status", "")),
        "version": getattr(announcement, "version", None),
    }


def build_announcement_tools(service: Any, context_provider: ContextProvider) -> dict[str, Tool]:
    def announcement_list(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_list", OperationLevel.READ)
        context = context_provider(state)
        search = AnnouncementSearch(
            statuses=tuple(state.slots.get("statuses") or ()),
            limit=int(state.slots.get("limit") or 20),
        )
        items = service.search(search, context)
        return ok("announcement_list", count=len(items), items=[_brief(i) for i in items])

    def announcement_get(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_get", OperationLevel.READ)
        context = context_provider(state)
        raw = require_slot(state, "announcement_id", "announcement_get")
        announcement = service.get(UUID(str(raw)), context)
        return ok("announcement_get", announcement=_brief(announcement))

    def announce_publish(state: GraphState) -> dict[str, Any]:
        """高风险：只转人工，绝不调用 ``service.publish``。"""
        assert_level("announce_publish", OperationLevel.WRITE_HIGH_RISK)
        return handover(
            "announce_publish",
            "公告发布属于高风险操作，需授权人员在管理端复核后发布。",
            title=state.slots.get("title"),
            category=state.slots.get("category"),
            announcement_id=state.slots.get("announcement_id"),
        )

    return {
        "announcement_list": announcement_list,
        "announcement_get": announcement_get,
        "announce_publish": announce_publish,
    }
