"""Canonical actions accepted by the announcement Agent subgraph."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import NamedTuple


class AnnouncementAgentAction(StrEnum):
    LIST = "list"
    GET = "get"
    DRAFT = "draft"
    REVISE = "revise"
    CREATE = "create"
    PUBLISH = "publish"
    SCHEDULE = "schedule"


_ALIASES: dict[str, AnnouncementAgentAction] = {
    "": AnnouncementAgentAction.LIST,
    "query": AnnouncementAgentAction.LIST,
    "search": AnnouncementAgentAction.LIST,
    "detail": AnnouncementAgentAction.GET,
    "polish": AnnouncementAgentAction.DRAFT,
    "write": AnnouncementAgentAction.DRAFT,
    "rewrite": AnnouncementAgentAction.REVISE,
    "edit": AnnouncementAgentAction.REVISE,
    "update": AnnouncementAgentAction.REVISE,
    "revise_draft": AnnouncementAgentAction.REVISE,
    "save": AnnouncementAgentAction.CREATE,
    "create_draft": AnnouncementAgentAction.CREATE,
    "release": AnnouncementAgentAction.PUBLISH,
    "send": AnnouncementAgentAction.PUBLISH,
    "schedule_publish": AnnouncementAgentAction.SCHEDULE,
}


def normalize_announcement_action(value: object) -> AnnouncementAgentAction | None:
    """Normalize model synonyms and reject unsupported action vocabulary."""

    raw = str(value or "").strip().lower()
    try:
        return AnnouncementAgentAction(raw)
    except ValueError:
        return _ALIASES.get(raw)


_ACTION_MARKERS: tuple[tuple[AnnouncementAgentAction, tuple[str, ...]], ...] = (
    (
        AnnouncementAgentAction.CREATE,
        (
            "采用这个稿件",
            "采用这份稿件",
            "采用该稿件",
            "采用该草稿",
            "采纳这个稿件",
            "采纳这份稿件",
            "采纳该稿件",
            "采纳",
            "采用",
            "使用这个稿件",
            "使用这份稿件",
            "使用该稿件",
            "保存草稿",
            "创建草稿",
        ),
    ),
    (AnnouncementAgentAction.PUBLISH, ("立即发布", "现在发布", "确认发布")),
    (AnnouncementAgentAction.SCHEDULE, ("定时发布", "预约发布", "到点发布")),
    (
        AnnouncementAgentAction.REVISE,
        ("重新润色", "修改稿件", "修改公告", "重写公告", "修改一下", "调整一下"),
    ),
)

_IMPLICIT_REVISION_CUES = (
    "修改",
    "调整",
    "改成",
    "改为",
    "换成",
    "删掉",
    "去掉",
    "加上",
    "补充",
    "具体",
    "详细",
    "简洁",
    "正式",
    "口语",
    "标题",
    "正文",
    "语气",
    "措辞",
    "时间",
    "日期",
    "受众",
)


class AnnouncementFollowup(NamedTuple):
    action: AnnouncementAgentAction | None
    instruction: str | None = None
    detail_kind: str | None = None
    slot_updates: dict[str, object] | None = None


_CHINESE_BUILDING_NUMBERS = {
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


def normalize_announcement_audience(value: object) -> dict[str, object]:
    """Convert chat/display audience values to the business object contract."""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        compact = value.strip()
        if compact in {"全社区", "所有住户", "全体住户"}:
            return {}
        try:
            decoded = json.loads(compact)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            return decoded
        matches = re.findall(r"(\d+|[一二两三四五六七八九十])\s*栋", compact)
        if matches:
            building_ids = [f"{_CHINESE_BUILDING_NUMBERS.get(item, item)}栋" for item in matches]
            return {"building_ids": list(dict.fromkeys(building_ids))}
    raise ValueError("公告受众格式无效，请重新选择受众范围。")


def _explicit_audience_update(text: str) -> dict[str, object]:
    if not any(marker in text for marker in ("受众", "对象", "范围")):
        return {}
    if any(marker in text for marker in ("全社区", "所有住户", "全体住户")):
        return {"audience": {}}
    try:
        audience = normalize_announcement_audience(text)
    except ValueError:
        return {}
    return {"audience": audience}


def resolve_announcement_followup(text: str, *, has_active_draft: bool) -> AnnouncementFollowup:
    """Resolve explicit actions and implicit feedback against an active draft."""

    compact = str(text or "").strip()
    slot_updates = _explicit_audience_update(compact)
    for action, markers in _ACTION_MARKERS:
        if any(marker in compact for marker in markers):
            instruction = compact if action == AnnouncementAgentAction.REVISE else None
            return AnnouncementFollowup(action, instruction, slot_updates=slot_updates)
    if not has_active_draft or len(compact) > 120:
        return AnnouncementFollowup(None, slot_updates=slot_updates)
    if not any(cue in compact for cue in _IMPLICIT_REVISION_CUES):
        return AnnouncementFollowup(None, slot_updates=slot_updates)
    asks_for_missing_time = (
        "具体时间" in compact
        and not any(char.isdigit() for char in compact)
        and not any(
            period in compact for period in ("凌晨", "早上", "上午", "中午", "下午", "晚上")
        )
    )
    return AnnouncementFollowup(
        AnnouncementAgentAction.REVISE,
        None if asks_for_missing_time else compact,
        "event_time" if asks_for_missing_time else None,
        slot_updates,
    )
