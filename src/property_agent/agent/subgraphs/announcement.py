"""公告子图：查询、AI起草、人工确认建稿及管理者确认发布。"""

from collections.abc import Mapping

from property_agent.agent.announcement_actions import (
    AnnouncementAgentAction,
    normalize_announcement_action,
    normalize_announcement_audience,
)
from property_agent.agent.graph_core import StateGraph
from property_agent.agent.selector_context import current_selector_context
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.base import attach_subgraph
from property_agent.agent.working_state import (
    AnnouncementDraftingState,
    AnnouncementPublishState,
    AnnouncementQueryState,
    EmptyWorkingState,
    synchronize_typed_domain,
)
from property_agent.announcement.domain.classification import (
    classify_announcement_category,
)

NAME = "announcement"


def select_announcement_tool(state: GraphState) -> str:
    if isinstance(state.domain, EmptyWorkingState) and state.intent is None:
        state.intent = "ANNOUNCEMENT"
    if isinstance(state.domain, EmptyWorkingState) and state.intent == "ANNOUNCEMENT":
        synchronize_typed_domain(state)
    domain = state.domain
    if not isinstance(
        domain,
        (AnnouncementQueryState, AnnouncementDraftingState, AnnouncementPublishState),
    ):
        return "announcement_list"
    action = normalize_announcement_action(getattr(domain, "action", None))
    # 角色守卫（确定性）：住户/无发布权限角色不得触发任何公告写动作。
    # 写工具名不允许进入 trace，越权请求直接以只读列表 + 明确拒绝收尾。
    trusted = current_selector_context()
    roles = set(trusted.roles) if trusted is not None else set()
    if not (roles & {"MANAGER", "SYSTEM_ADMIN", "CUSTOMER_SERVICE"}):
        if action in {
            AnnouncementAgentAction.CREATE,
            AnnouncementAgentAction.PUBLISH,
            AnnouncementAgentAction.SCHEDULE,
        }:
            state.error = "住户没有发布公告的权限，请联系物业或客服代为发布。"
            state.slots["action"] = "list"
            return "announcement_list"
    # 用户带着新公告内容说"发布/定时发布"（无 announcement_id）→ 实为新建，
    # 降级为建稿流程，而不是追问一条已存在公告的编号。
    if (
        action in {AnnouncementAgentAction.PUBLISH, AnnouncementAgentAction.SCHEDULE}
        and not getattr(domain, "announcement_id", None)
        and getattr(domain, "title", None)
        and getattr(domain, "body", None)
    ):
        action = AnnouncementAgentAction.CREATE
        state.slots["action"] = "create"
    if action in {AnnouncementAgentAction.REVISE, AnnouncementAgentAction.CREATE}:
        active_draft = state.slots.get("_active_announcement_draft")
        if isinstance(active_draft, dict):
            for field in ("title", "body", "audience"):
                if state.slots.get(field) is None and active_draft.get(field) is not None:
                    state.slots[field] = active_draft[field]
        title = state.slots.get("title")
        body = state.slots.get("body")
        if isinstance(title, str) and isinstance(body, str) and title.strip() and body.strip():
            state.slots["category"] = classify_announcement_category(title, body).value
        if state.slots.get("audience") is not None:
            normalized = normalize_announcement_audience(state.slots["audience"])
            if normalized is not None:
                state.slots["audience"] = normalized
            else:
                # 无法归一化：清空槽位，交由 collect_slots 引导用户澄清受众范围。
                state.slots["audience"] = None
    if action == AnnouncementAgentAction.DRAFT:
        return "announcement_draft"
    if action == AnnouncementAgentAction.REVISE:
        return "announcement_revise"
    if action == AnnouncementAgentAction.CREATE:
        return "announcement_create_draft"
    if action == AnnouncementAgentAction.SCHEDULE:
        if not state.slots.get("expected_version"):
            return "announcement_get"
        return "announcement_schedule_publish"
    if action == AnnouncementAgentAction.PUBLISH:
        if not state.slots.get("expected_version"):
            return "announcement_get"
        return "announce_publish"
    if action == AnnouncementAgentAction.GET or state.slots.get("announcement_id"):
        return "announcement_get"
    return "announcement_list"


def attach_announcement_subgraph(graph: StateGraph, registry: Mapping) -> str:
    return attach_subgraph(graph, name=NAME, selector=select_announcement_tool, registry=registry)
