"""Deterministic safety floor for user-stated repair semantics."""

from __future__ import annotations

import re
from typing import Any

from property_agent.repair.domain.classification import classify_repair_category

_CREATE_MARKERS = (
    "帮我报修",
    "我要报修",
    "需要报修",
    "申请报修",
    "提交报修",
    "新建报修",
    "报一个",
    "帮我提交",
    "帮我处理",
    "处理吧",
)
_QUERY_MARKERS = ("看看", "查询", "查看", "有哪些", "进度", "记录", "未完成")
_LOCATIONS = ("卫生间", "厨房", "客厅", "卧室", "阳台", "玄关", "楼道", "地下车库", "车库")
_HIGH_RISK = ("燃气", "煤气", "明火", "着火", "漏电", "有人被困", "人身危险")
_URGENT = ("比较急", "很急", "紧急", "尽快", "马上", "立刻")
_OPEN_STATUSES = (
    "PENDING_ASSIGNMENT",
    "PENDING_ACCEPTANCE",
    "PROCESSING",
    "PENDING_VERIFICATION",
    "REWORKING",
)


def explicit_repair_creation(text: str) -> bool:
    return any(marker in text for marker in _CREATE_MARKERS)


def repair_query_requested(text: str) -> bool:
    return any(marker in text for marker in _QUERY_MARKERS) and not explicit_repair_creation(text)


def normalize_repair_create(text: str, values: dict[str, Any]) -> dict[str, Any]:
    """Keep model extraction bounded and enforce user-observable safety cues."""
    location = str(values.get("location") or _location(text) or "").strip()
    description = _clean_description(str(values.get("description") or ""), text, location)
    urgency = _urgency_floor(text, str(values.get("urgency") or "NORMAL"))
    category = classify_repair_category(description or text).value
    result = {
        "category": category,
        "location": location,
        "description": description,
        "urgency": urgency,
    }
    for key in ("contact_name", "contact_phone", "access_instructions"):
        if values.get(key):
            result[key] = str(values[key]).strip()
    preferred = tuple(values.get("preferred_time_windows") or ())
    if not preferred:
        preferred = tuple(marker for marker in ("明天上午", "明天下午", "周末") if marker in text)
    if preferred:
        result["preferred_time_windows"] = preferred[:5]
    return result


def normalize_repair_list(text: str, values: dict[str, Any]) -> dict[str, Any]:
    statuses = tuple(values.get("statuses") or ())
    if "未完成" in text and not statuses:
        statuses = _OPEN_STATUSES
    return {"statuses": statuses, "limit": int(values.get("limit") or 20)}


def _location(text: str) -> str | None:
    return next((item for item in _LOCATIONS if item in text), None)


def _urgency_floor(text: str, proposed: str) -> str:
    if any(marker in text for marker in _HIGH_RISK):
        return "HIGH_RISK"
    if any(marker in text for marker in _URGENT):
        return "URGENT"
    normalized = proposed.upper()
    return normalized if normalized in {"NORMAL", "URGENT", "HIGH_RISK"} else "NORMAL"


def _clean_description(proposed: str, text: str, location: str) -> str:
    value = proposed.strip()
    if not value or value == text.strip():
        value = re.sub(r"^(?:帮我报修|我要报修|需要报修|申请报修)[，,:：\s]*", "", text)
        value = re.sub(
            r"[，,。\s]*(?:帮我报修|我要报修|需要报修|申请报修|提交报修)[。！!\s]*$",
            "",
            value,
        )
        value = re.sub(r"^\d+栋\d+单元\d+(?:室)?", "", value).lstrip("，,。 ")
        if location and value.startswith(location):
            value = value[len(location) :]
        value = re.sub(r"[，,。\s]*(?:比较急|很急|紧急|请尽快|尽快)[。！!\s]*$", "", value)
    return value.strip("，,。 ")
