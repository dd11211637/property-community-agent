"""公告工具：AI只起草文本，业务写入统一调用公开 AnnouncementService。"""

import re
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from property_agent.agent.announcement_actions import normalize_announcement_audience
from property_agent.agent.announcement_time import (
    materialize_relative_dates,
    temporal_writing_guidance,
)
from property_agent.agent.capabilities.adapters.announcement import (
    AnnouncementCreateAdapter,
    AnnouncementDraftAdapter,
    AnnouncementGetAdapter,
    AnnouncementListAdapter,
    AnnouncementPublishAdapter,
    AnnouncementReviseAdapter,
    AnnouncementScheduleAdapter,
    CommunityKnowledgeAdapter,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import CapabilityWriteContext
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import default_capability_policy
from property_agent.agent.policies import OperationLevel
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import (
    ContextProvider,
    Tool,
    ToolPreconditionError,
    assert_level,
    idempotency_key,
    ok,
    require_confirmation,
    require_slot,
)
from property_agent.agent.tools.capability_bridge import invoke_capability
from property_agent.announcement.domain.classification import (
    classify_announcement_category,
)
from property_agent.platform.roles import Role


def _require_audience(state: GraphState, tool: str) -> dict[str, object]:
    """归一化公告受众槽位；无法解析时抛前置错误（由 execute_tool 转错误提示）。"""
    audience = normalize_announcement_audience(require_slot(state, "audience", tool))
    if audience is None:
        raise ToolPreconditionError(f"{tool} 公告受众格式无效，请重新选择受众范围。")
    return audience


def _brief(announcement: Any) -> dict[str, Any]:
    def value(name: str) -> Any:
        raw = getattr(announcement, name, None)
        return getattr(raw, "value", raw)

    return {
        "entity_type": "ANNOUNCEMENT",
        "id": str(announcement.id),
        "business_no": value("business_no"),
        "title": value("title"),
        "body": value("body"),
        "category": value("category"),
        "status": value("status"),
        "audience": value("audience_condition") or {},
        "scheduled_at": str(value("scheduled_at") or "") or None,
        "published_at": str(value("published_at") or "") or None,
        "version": getattr(announcement, "version", None),
    }


_TOPIC_TERMS = {
    "WATER_OUTAGE": ("停水", "供水中断", "供水维护", "水管抢修"),
    "POWER_OUTAGE": ("停电", "供电中断", "供电维护", "电力检修"),
}


def _effective_date(announcement: Any) -> date | None:
    scheduled_at = getattr(announcement, "scheduled_at", None)
    if scheduled_at is not None:
        return scheduled_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    text = f"{getattr(announcement, 'title', '')} {getattr(announcement, 'body', '')}"
    match = re.search(
        r"(?P<year>20\d{2})[年/-](?P<month>1[0-2]|0?[1-9])"
        r"[月/-](?P<day>3[01]|[12]\d|0?[1-9])日?",
        text,
    )
    if match:
        try:
            return date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            return None
    published_at = getattr(announcement, "published_at", None)
    if published_at is not None:
        published_date = published_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if any(marker in text for marker in ("今天", "今日")):
            return published_date
        if any(marker in text for marker in ("明天", "明日")):
            return published_date + timedelta(days=1)
    return None


def _effective_date_brief(announcement: dict[str, Any]) -> date | None:
    for key in ("scheduled_at", "published_at"):
        raw = announcement.get(key)
        if raw:
            return datetime.fromisoformat(str(raw)).astimezone(ZoneInfo("Asia/Shanghai")).date()
    return None


def _announcement_executor(service, model_gateway, provided):
    if provided is not None:
        return provided
    gateway = model_gateway
    if gateway is None:
        from property_agent.agent.model_gateway import DeterministicModelGateway

        gateway = DeterministicModelGateway()
    return CapabilityExecutor(
        default_capability_registry(),
        default_capability_policy(),
        {
            "announcement_list": AnnouncementListAdapter(service),
            "announcement_get": AnnouncementGetAdapter(service),
            "community_knowledge_search": CommunityKnowledgeAdapter(service),
            "announcement_draft": AnnouncementDraftAdapter(gateway),
            "announcement_revise": AnnouncementReviseAdapter(gateway),
            "announcement_create_draft": AnnouncementCreateAdapter(service),
            "announce_publish": AnnouncementPublishAdapter(service),
            "announcement_schedule_publish": AnnouncementScheduleAdapter(service),
        },
    )


def build_announcement_tools(
    service: Any,
    context_provider: ContextProvider,
    model_gateway: Any | None = None,
    capability_executor: CapabilityExecutor | None = None,
) -> dict[str, Tool]:
    executor = _announcement_executor(service, model_gateway, capability_executor)

    invoke = partial(invoke_capability, executor, context_provider)

    def announcement_list(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_list", OperationLevel.READ)
        data = invoke(
            state,
            "announcement_list",
            {
                "statuses": tuple(state.slots.get("statuses") or ()),
                "limit": int(state.slots.get("limit") or 20),
            },
        )
        items = list(data["items"])
        topic = str(state.slots.get("topic") or "").upper()
        target_date_text = str(state.slots.get("target_date") or "").strip()
        if topic in _TOPIC_TERMS:
            terms = _TOPIC_TERMS[topic]
            items = [
                item
                for item in items
                if any(term in f"{item.get('title', '')} {item.get('body', '')}" for term in terms)
            ]
        undated_matches = 0
        if target_date_text:
            try:
                target_date = date.fromisoformat(target_date_text)
            except ValueError:
                target_date = None
            if target_date is not None:
                undated_matches = sum(_effective_date_brief(item) is None for item in items)
                items = [item for item in items if _effective_date_brief(item) == target_date]
        return ok(
            "announcement_list",
            count=len(items),
            items=items,
            topic=topic or None,
            target_date=target_date_text or None,
            undated_matches=undated_matches,
            query_scope={
                key: state.trusted_context.get(key)
                for key in ("community_name", "building", "house_display")
                if state.trusted_context.get(key)
            },
        )

    def announcement_get(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_get", OperationLevel.READ)
        raw = require_slot(state, "announcement_id", "announcement_get")
        announcement = invoke(state, "announcement_get", {"announcement_id": UUID(str(raw))})[
            "announcement"
        ]
        state.slots.update(
            {
                "announcement_id": announcement["id"],
                "expected_version": announcement["version"],
                "title": announcement["title"],
                "body": announcement["body"],
                "category": announcement["category"],
                "audience": announcement["audience"],
            }
        )
        return ok("announcement_get", announcement=announcement)

    def community_knowledge_search(state: GraphState) -> dict[str, Any]:
        """Search only resident-visible published material; never synthesize rules."""
        assert_level("community_knowledge_search", OperationLevel.READ)
        query = str(require_slot(state, "query", "community_knowledge_search")).strip()
        limit = min(int(state.slots.get("limit") or 10), 20)
        data = invoke(state, "community_knowledge_search", {"query": query, "limit": limit})
        matches = data["items"]
        return ok(
            "community_knowledge_search",
            count=len(matches),
            items=[
                {
                    **item,
                    "source_name": item.get("title", "社区公告"),
                    "applicability": item.get("audience", {}) or {},
                }
                for item in matches
            ],
            query=query,
            source_scope="PUBLISHED_ANNOUNCEMENTS",
            query_scope={
                key: state.trusted_context.get(key)
                for key in ("community_name", "building", "house_display")
                if state.trusted_context.get(key)
            },
        )

    def announcement_draft(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_draft", OperationLevel.READ)
        topic = str(require_slot(state, "topic", "announcement_draft")).strip()
        audience = _require_audience(state, "announcement_draft")
        requirements = str(state.slots.get("requirements") or state.slots.get("user_text") or "")
        time_guidance = temporal_writing_guidance(
            target_date=state.slots.get("target_date"),
            scheduled_at=state.slots.get("scheduled_at"),
        )
        if time_guidance:
            requirements = f"{requirements}\n服务端可信时间事实：{time_guidance}。"
        draft = invoke(
            state,
            "announcement_draft",
            {"topic": topic, "audience": audience, "requirements": requirements},
        )["draft"]
        for field in ("title", "body"):
            if isinstance(draft.get(field), str):
                draft[field] = materialize_relative_dates(
                    draft[field], target_date=state.slots.get("target_date")
                )
        draft["category"] = classify_announcement_category(
            str(draft.get("title") or ""), str(draft.get("body") or "")
        ).value
        state.slots.update(draft)
        state.slots["audience"] = audience
        state.slots["action"] = "create"
        return ok(
            "announcement_draft",
            draft={**draft, "audience": audience},
        )

    def announcement_revise(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_revise", OperationLevel.READ)
        audience = _require_audience(state, "announcement_revise")
        instruction = str(
            require_slot(state, "revision_instruction", "announcement_revise")
        ).strip()
        time_guidance = temporal_writing_guidance(
            target_date=state.slots.get("target_date"),
            scheduled_at=state.slots.get("scheduled_at"),
        )
        if time_guidance:
            instruction = f"{instruction}\n服务端可信时间事实：{time_guidance}。"
        current = {
            key: str(require_slot(state, key, "announcement_revise")).strip()
            for key in ("title", "body")
        }
        current["category"] = classify_announcement_category(
            current["title"], current["body"]
        ).value
        revised = invoke(
            state,
            "announcement_revise",
            {
                "title": current["title"],
                "body": current["body"],
                "category": current["category"],
                "audience": audience,
                "revision_instruction": instruction,
            },
        )["draft"]
        for field in ("title", "body"):
            if isinstance(revised.get(field), str):
                revised[field] = materialize_relative_dates(
                    revised[field], target_date=state.slots.get("target_date")
                )
        for field in ("title", "body"):
            value = revised.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("AI 返回的公告修改结果不完整，请重新修改稿件。")
        revised["category"] = classify_announcement_category(
            revised["title"], revised["body"]
        ).value
        state.slots.update(revised)
        state.slots["audience"] = audience
        state.slots["action"] = "create"
        state.slots.pop("revision_instruction", None)
        state.slots.pop("revision_detail_kind", None)
        return ok(
            "announcement_revise",
            draft={**revised, "audience": audience},
        )

    def announcement_create_draft(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_create_draft", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "announcement_create_draft")
        title = str(require_slot(state, "title", "announcement_create_draft"))
        body = str(require_slot(state, "body", "announcement_create_draft"))
        params = {
            "title": title,
            "body": body,
            "category": classify_announcement_category(title, body).value,
            "audience": _require_audience(state, "announcement_create_draft"),
        }
        announcement = invoke(
            state,
            "announcement_create_draft",
            {"title": title, "body": body, "audience": params["audience"]},
            confirmed=True,
            write=CapabilityWriteContext(
                confirmation_token=token,
                approval_ref=state.approval_ref,
                idempotency_key=idempotency_key(state, "announcement_create_draft", params),
            ),
        )["announcement"]
        state.slots.update(
            {
                "announcement_id": announcement["id"],
                "expected_version": announcement["version"],
            }
        )
        return ok("announcement_create_draft", announcement=announcement)

    def _approved_for_manager(state: GraphState, tool: str):
        context = context_provider(state)
        if not context.has_any_role(Role.MANAGER):
            raise PermissionError("只有管理者可以确认发布公告。")
        announcement = invoke(
            state,
            "announcement_get",
            {"announcement_id": UUID(str(require_slot(state, "announcement_id", tool)))},
        )["announcement"]
        reviewed_version = int(require_slot(state, "expected_version", tool))
        if reviewed_version != announcement["version"]:
            raise RuntimeError("公告内容已发生变化，请重新查看后再确认发布。")
        return context, announcement

    def announce_publish(state: GraphState) -> dict[str, Any]:
        assert_level("announce_publish", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "announce_publish")
        _context, announcement = _approved_for_manager(state, "announce_publish")
        published = invoke(
            state,
            "announce_publish",
            {
                "announcement_id": announcement["id"],
                "expected_version": announcement["version"],
            },
            confirmed=True,
            write=CapabilityWriteContext(
                confirmation_token=token,
                approval_ref=state.approval_ref,
                idempotency_key=idempotency_key(
                    state,
                    "announce_publish",
                    {"id": announcement["id"], "version": announcement["version"]},
                ),
            ),
        )["announcement"]
        return ok("announce_publish", announcement=published)

    def announcement_schedule_publish(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_schedule_publish", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "announcement_schedule_publish")
        context, announcement = _approved_for_manager(state, "announcement_schedule_publish")
        scheduled_at = datetime.fromisoformat(
            str(require_slot(state, "scheduled_at", "announcement_schedule_publish"))
        )
        scheduled = invoke(
            state,
            "announcement_schedule_publish",
            {
                "announcement_id": announcement["id"],
                "expected_version": announcement["version"],
                "scheduled_at": scheduled_at,
            },
            confirmed=True,
            write=CapabilityWriteContext(
                confirmation_token=token,
                approval_ref=state.approval_ref,
                idempotency_key=idempotency_key(
                    state,
                    "announcement_schedule_publish",
                    {
                        "id": announcement["id"],
                        "version": announcement["version"],
                        "scheduled_at": scheduled_at,
                    },
                ),
            ),
        )["announcement"]
        return ok("announcement_schedule_publish", announcement=scheduled)

    return {
        "announcement_list": announcement_list,
        "announcement_get": announcement_get,
        "community_knowledge_search": community_knowledge_search,
        "announcement_draft": announcement_draft,
        "announcement_revise": announcement_revise,
        "announcement_create_draft": announcement_create_draft,
        "announce_publish": announce_publish,
        "announcement_schedule_publish": announcement_schedule_publish,
    }
