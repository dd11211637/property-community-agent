"""Capability-scoped fact resolution for active inspection-event tasks."""

from __future__ import annotations

import re
from typing import Any

from property_agent.inspection.domain.classification import normalize_security_event

_NON_FACT_REPLIES = {
    "其他",
    "其他事件",
    "是",
    "不是",
    "普通",
    "较高",
    "紧急",
}
_VAGUE_EVENT_TEXT = (
    re.compile(r"^.{0,20}(?:有|发现)?异常[了。！!]*$"),
    re.compile(r"^.{0,20}(?:情况|事件)不明[。！!]*$"),
)
_LOCATION_PATTERN = re.compile(
    r"\d+栋(?:\d+单元)?(?:厨房|客厅|卧室|楼道)?|"
    r"地下车库出口|地下车库|安全出口|消防通道|疏散通道|车库出口|"
    r"楼栋大厅|小区出入口|公共设备间|设备间|厨房|客厅|卧室|楼道"
)
_DETAIL_CUES = (
    "燃气",
    "煤气",
    "烟",
    "火",
    "堵",
    "堆",
    "纸箱",
    "自行车",
    "电动车",
    "受伤",
    "被困",
    "摔倒",
    "损坏",
    "故障",
    "异味",
    "气味",
    "无法通行",
    "不能通行",
)


def resolve_inspection_event_facts(
    current: dict[str, Any],
    user_text: str,
    *,
    correction: bool = False,
) -> dict[str, Any]:
    """Resolve facts inside ``security_event_create`` without replanning intent."""

    updates: dict[str, Any] = {}
    text = str(user_text or "").strip()
    if not text:
        return updates
    locations = _LOCATION_PATTERN.findall(text)
    if locations:
        updates["location"] = locations[-1] if correction else locations[0]
    detail = _fact_description(text, correction=correction)
    existing = str(current.get("description") or "").strip()
    if detail:
        updates["description"] = _merge_description(existing, detail, correction=correction)
    description = str(updates.get("description") or existing)
    if description:
        normalized = normalize_security_event(description, current.get("risk_level"))
        updates["event_type"] = normalized.event_type.value
        updates["risk_level"] = normalized.risk_level.value
    return updates


def _fact_description(text: str, *, correction: bool) -> str | None:
    compact = "".join(text.split())
    vague = any(pattern.fullmatch(text) for pattern in _VAGUE_EVENT_TEXT)
    if compact in _NON_FACT_REPLIES or vague:
        return None
    if correction and not any(cue in text for cue in _DETAIL_CUES):
        return None
    return text if len(compact) >= 3 else None


def _merge_description(existing: str, detail: str, *, correction: bool) -> str:
    if not existing or existing == detail:
        return detail
    if correction:
        return existing
    if detail in existing:
        return existing
    return f"{existing}；{detail}"


__all__ = ["resolve_inspection_event_facts"]
