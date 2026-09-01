"""User-facing slot prompts for guided business conversations.

The graph keeps validation deterministic, while this module translates an active
business field into Chinese questions and safe UI choices.  Values remain business
inputs supplied by the user; no option is treated as a verified business fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from property_agent.agent.state import GraphState

_REPAIR_FIELD_LABELS = {
    "description": "故障现象",
    "location": "发生地点",
    "appointment_at": "预约上门时间",
}
_ANNOUNCEMENT_FIELD_LABELS = {
    "title": "公告标题",
    "body": "公告正文",
    "audience": "受众范围",
}
_INSPECTION_FIELD_LABELS = {
    "title": "任务标题",
    "description": "巡检要求",
    "point": "巡检点位",
}
_SECURITY_EVENT_FIELD_LABELS = {
    "location": "发生位置",
    "description": "现场情况",
    "event_type": "现场情况",
    "risk_level": "风险事实",
}


def _option(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def _format_datetime(value: Any) -> str:
    """Format a datetime or ISO string for the slot completion summary."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value).strip()
    if not text:
        return text
    # Try to normalise a partial ISO string to a friendly display.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _repair_completed(state: GraphState) -> list[dict[str, str]]:
    completed: list[dict[str, str]] = []
    for field in ("description", "location", "appointment_at"):
        value = state.slots.get(field)
        if value is None or not str(value).strip():
            continue
        display = _format_datetime(value) if field == "appointment_at" else str(value).strip()
        completed.append(
            {
                "field": field,
                "label": _REPAIR_FIELD_LABELS[field],
                "value": display,
            }
        )
    return completed


def repair_slot_prompt(state: GraphState) -> dict[str, Any] | None:
    """Build the next single-question prompt for a repair creation flow."""

    if state.intent != "REPAIR" or not state.requested_slot:
        return None
    field = state.requested_slot
    completed = _repair_completed(state)
    common: dict[str, Any] = {
        "field": field,
        "step": len(completed) + 1,
        "total_steps": 3,
        "completed": completed,
    }
    if field == "description":
        return {
            **common,
            "label": "故障现象",
            "prompt": "请描述一下具体出现了什么故障？",
            "help_text": (
                "例如：水管一直漏水、插座频繁跳闸、电梯门无法关闭。系统会根据描述自动归类。"
            ),
            "allow_custom": True,
            "options": [],
        }
    if field == "location":
        return {
            **common,
            "label": "发生地点",
            "prompt": "这个故障发生在哪里？",
            "help_text": "可选择常用位置，也可以输入楼栋、单元或更具体的位置。",
            "allow_custom": True,
            "options": [
                _option(value, value)
                for value in (
                    "客厅",
                    "卧室",
                    "厨房",
                    "卫生间",
                    "阳台",
                    "玄关",
                    "楼道或公共区域",
                    "地下车库",
                )
            ],
        }
    if field == "appointment_at":
        return {
            **common,
            "label": "预约上门时间",
            "prompt": "你希望什么时候上门维修？",
            "help_text": (
                "请选择或输入上门时间，建议格式 2026-08-31 15:00。"
                '如果暂时无法确定，可以输入"稍后协商"，系统会先提交工单。'
            ),
            "allow_custom": True,
            "options": [],
        }
    return None


def _announcement_completed(state: GraphState) -> list[dict[str, str]]:
    completed: list[dict[str, str]] = []
    for field in ("title", "body", "audience"):
        if field not in state.slots or state.slots[field] is None:
            continue
        value = state.slots[field]
        if field == "audience":
            display = _audience_display(value)
        else:
            display = str(value).strip()
        if display:
            completed.append(
                {"field": field, "label": _ANNOUNCEMENT_FIELD_LABELS[field], "value": display}
            )
    return completed


def _audience_display(value: Any) -> str:
    if value == {}:
        return "全社区"
    if isinstance(value, dict):
        buildings = value.get("building_ids")
        if isinstance(buildings, list) and buildings:
            return "、".join(str(item) for item in buildings) + "住户"
    return str(value).strip()


def announcement_slot_prompt(state: GraphState) -> dict[str, Any] | None:
    """Build progressive fields for creating a new announcement draft."""

    field = state.requested_slot
    if state.intent != "ANNOUNCEMENT" or field not in _ANNOUNCEMENT_FIELD_LABELS:
        return None
    completed = _announcement_completed(state)
    common: dict[str, Any] = {
        "field": field,
        "label": _ANNOUNCEMENT_FIELD_LABELS[field],
        "step": len(completed) + 1,
        "total_steps": 3,
        "completed": completed,
        "allow_custom": True,
    }
    if field == "title":
        return {
            **common,
            "prompt": "请填写公告标题",
            "help_text": "简洁说明事项和影响范围，例如：1栋临时停水通知。",
            "options": [],
        }
    if field == "body":
        return {
            **common,
            "prompt": "请填写公告正文",
            "help_text": "建议写明时间、影响范围、处理安排和需要住户配合的事项。",
            "options": [],
        }
    return {
        **common,
        "prompt": "请选择公告受众，也可以输入具体楼栋",
        "help_text": "受众范围将用于后续审核和发布，不会在此步骤直接发布。",
        "options": [
            {"label": "全社区", "value": {}},
            {"label": "1栋住户", "value": {"building_ids": ["1栋"]}},
            {"label": "2栋住户", "value": {"building_ids": ["2栋"]}},
        ],
    }


def inspection_slot_prompt(state: GraphState) -> dict[str, Any] | None:
    """Build the progressive form for creating an inspection task."""

    field = state.requested_slot
    if state.intent == "INSPECTION" and state.slots.get("action") == "report_event":
        return _security_event_slot_prompt(state)
    if (
        state.intent != "INSPECTION"
        or state.slots.get("action") != "create"
        or field not in _INSPECTION_FIELD_LABELS
    ):
        return None
    completed = [
        {
            "field": name,
            "label": _INSPECTION_FIELD_LABELS[name],
            "value": str(state.slots[name]).strip(),
        }
        for name in ("title", "description", "point")
        if state.slots.get(name) is not None and str(state.slots[name]).strip()
    ]
    common: dict[str, Any] = {
        "field": field,
        "label": _INSPECTION_FIELD_LABELS[field],
        "step": len(completed) + 1,
        "total_steps": 3,
        "completed": completed,
        "allow_custom": True,
    }
    if field == "title":
        return {
            **common,
            "prompt": "请填写巡检任务标题",
            "help_text": "概括本次巡检主题，例如：每周小区安防巡检。",
            "options": [],
        }
    if field == "description":
        return {
            **common,
            "prompt": "请说明本次巡检要求",
            "help_text": "说明需要检查的设施、风险或完成标准。",
            "options": [],
        }
    return {
        **common,
        "prompt": "请选择巡检点位，也可以输入更具体的位置",
        "help_text": "创建后可在巡检工作台继续分派和跟踪任务。",
        "options": [
            _option(value, value)
            for value in (
                "小区出入口",
                "楼栋大厅",
                "消防通道",
                "地下车库",
                "公共设备间",
            )
        ],
    }


def _security_event_slot_prompt(state: GraphState) -> dict[str, Any] | None:
    field = state.requested_slot
    if field not in _SECURITY_EVENT_FIELD_LABELS:
        return None
    prompt_field = "description" if field in {"event_type", "risk_level"} else field
    if prompt_field == "location":
        prompt = "请说明异常发生的具体位置。"
        help_text = "可以直接说安全出口、消防通道、地下车库出口等实际位置。"
    else:
        prompt = "请描述现场具体发生了什么，以及是否影响通行或人员安全。"
        help_text = "请说看到、闻到或受到影响的事实；系统会自动判断事件分类和风险下限。"
    return {
        "field": prompt_field,
        "label": _SECURITY_EVENT_FIELD_LABELS[field],
        "prompt": prompt,
        "help_text": help_text,
        "allow_custom": True,
        "options": [],
    }
