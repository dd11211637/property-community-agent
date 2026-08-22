"""公告工具：AI只起草文本，业务写入统一调用公开 AnnouncementService。"""

import re
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from property_agent.agent.announcement_actions import normalize_announcement_audience
from property_agent.agent.announcement_time import (
    materialize_relative_dates,
    temporal_writing_guidance,
)
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
from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
    ReviewActionCommand,
    ScheduleAnnouncementCommand,
)
from property_agent.announcement.domain.classification import (
    classify_announcement_category,
)
from property_agent.announcement.domain.enums import AnnouncementAction, VersionSource
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


def build_announcement_tools(
    service: Any, context_provider: ContextProvider, model_gateway: Any | None = None
) -> dict[str, Tool]:
    def announcement_list(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_list", OperationLevel.READ)
        context = context_provider(state)
        search = AnnouncementSearch(
            statuses=tuple(state.slots.get("statuses") or ()),
            limit=int(state.slots.get("limit") or 20),
        )
        items = service.search(search, context)
        topic = str(state.slots.get("topic") or "").upper()
        target_date_text = str(state.slots.get("target_date") or "").strip()
        if topic in _TOPIC_TERMS:
            terms = _TOPIC_TERMS[topic]
            items = [
                item
                for item in items
                if any(
                    term in f"{getattr(item, 'title', '')} {getattr(item, 'body', '')}"
                    for term in terms
                )
            ]
        undated_matches = 0
        if target_date_text:
            try:
                target_date = date.fromisoformat(target_date_text)
            except ValueError:
                target_date = None
            if target_date is not None:
                undated_matches = sum(_effective_date(item) is None for item in items)
                items = [item for item in items if _effective_date(item) == target_date]
        return ok(
            "announcement_list",
            count=len(items),
            items=[_brief(i) for i in items],
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
        context = context_provider(state)
        raw = require_slot(state, "announcement_id", "announcement_get")
        announcement = service.get(UUID(str(raw)), context)
        state.slots.update(
            {
                "announcement_id": str(announcement.id),
                "expected_version": announcement.version,
                "title": announcement.title,
                "body": announcement.body,
                "category": announcement.category.value,
                "audience": announcement.audience_condition,
            }
        )
        return ok("announcement_get", announcement=_brief(announcement))

    def community_knowledge_search(state: GraphState) -> dict[str, Any]:
        """Search only resident-visible published material; never synthesize rules."""
        assert_level("community_knowledge_search", OperationLevel.READ)
        context = context_provider(state)
        query = str(require_slot(state, "query", "community_knowledge_search")).strip()
        limit = min(int(state.slots.get("limit") or 10), 20)
        candidates = service.search(AnnouncementSearch(statuses=("PUBLISHED",), limit=20), context)
        domain_terms = (
            "物业电话",
            "联系方式",
            "停车",
            "装修",
            "门禁",
            "垃圾",
            "开放时间",
            "社区规定",
            "物业规定",
        )
        terms = [term for term in domain_terms if term in query]
        if any(term in query for term in ("物业电话", "联系方式", "联系电话")):
            terms.extend(("物业电话", "联系方式", "联系电话"))
        terms.extend(
            term for term in re.split(r"[\s，。？！、,.!?]+", query) if 2 <= len(term) <= 8
        )
        matches = [
            item
            for item in candidates
            if any(
                term in f"{getattr(item, 'title', '')} {getattr(item, 'body', '')}"
                for term in terms
            )
        ][:limit]
        return ok(
            "community_knowledge_search",
            count=len(matches),
            items=[
                {
                    **_brief(item),
                    "source_name": getattr(item, "title", "社区公告"),
                    "applicability": getattr(item, "audience_condition", {}) or {},
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
        gateway = model_gateway
        if gateway is None or not hasattr(gateway, "draft_announcement"):
            from property_agent.agent.model_gateway import DeterministicModelGateway

            gateway = DeterministicModelGateway()
        draft = gateway.draft_announcement(
            topic=topic, audience=audience, requirements=requirements
        )
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
        gateway = model_gateway
        if gateway is None or not hasattr(gateway, "revise_announcement"):
            from property_agent.agent.model_gateway import DeterministicModelGateway

            gateway = DeterministicModelGateway()
        revised = gateway.revise_announcement(
            draft=current,
            audience=audience,
            instruction=instruction,
        )
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
        require_confirmation(state, "announcement_create_draft")
        context = context_provider(state)
        title = str(require_slot(state, "title", "announcement_create_draft"))
        body = str(require_slot(state, "body", "announcement_create_draft"))
        params = {
            "title": title,
            "body": body,
            "category": classify_announcement_category(title, body).value,
            "audience": _require_audience(state, "announcement_create_draft"),
        }
        announcement = service.create_draft(
            CreateAnnouncementCommand(
                str(params["title"]),
                str(params["body"]),
                str(params["category"]),
                params["audience"],
                source=VersionSource.AI_SUGGESTION_ADOPTED,
            ),
            context,
            idempotency_key=idempotency_key(state, "announcement_create_draft", params),
        )
        state.slots.update(
            {
                "announcement_id": str(announcement.id),
                "expected_version": announcement.version,
            }
        )
        return ok("announcement_create_draft", announcement=_brief(announcement))

    def _approved_for_manager(state: GraphState, tool: str):
        context = context_provider(state)
        if not context.has_any_role(Role.MANAGER):
            raise PermissionError("只有管理者可以确认发布公告。")
        announcement = service.get(UUID(str(require_slot(state, "announcement_id", tool))), context)
        reviewed_version = int(require_slot(state, "expected_version", tool))
        if reviewed_version != announcement.version:
            raise RuntimeError("公告内容已发生变化，请重新查看后再确认发布。")
        return context, announcement

    def announce_publish(state: GraphState) -> dict[str, Any]:
        assert_level("announce_publish", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "announce_publish")
        context, announcement = _approved_for_manager(state, "announce_publish")
        published = service.publish(
            announcement.id,
            ReviewActionCommand(
                AnnouncementAction.PUBLISH,
                int(require_slot(state, "expected_version", "announce_publish")),
                confirmation_token=token,
                approval_ref=state.approval_ref,
            ),
            context,
            idempotency_key=idempotency_key(
                state,
                "announce_publish",
                {"id": str(announcement.id), "version": announcement.version},
            ),
        )
        return ok("announce_publish", announcement=_brief(published))

    def announcement_schedule_publish(state: GraphState) -> dict[str, Any]:
        assert_level("announcement_schedule_publish", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "announcement_schedule_publish")
        context, announcement = _approved_for_manager(state, "announcement_schedule_publish")
        scheduled_at = datetime.fromisoformat(
            str(require_slot(state, "scheduled_at", "announcement_schedule_publish"))
        )
        scheduled = service.schedule_publish(
            announcement.id,
            ScheduleAnnouncementCommand(
                announcement.version, scheduled_at, token, approval_ref=state.approval_ref
            ),
            context,
            idempotency_key=idempotency_key(
                state,
                "announcement_schedule_publish",
                {
                    "id": str(announcement.id),
                    "version": announcement.version,
                    "scheduled_at": scheduled_at,
                },
            ),
        )
        return ok("announcement_schedule_publish", announcement=_brief(scheduled))

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
