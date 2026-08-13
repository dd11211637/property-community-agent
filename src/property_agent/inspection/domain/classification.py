"""Deterministic security-event classification and minimum-risk rules."""

from __future__ import annotations

from property_agent.inspection.domain.enums import EventRiskLevel, EventType

_EVENT_CUES: tuple[tuple[EventType, tuple[str, ...]], ...] = (
    (EventType.GAS_LEAK, ("燃气泄漏", "煤气泄漏", "燃气味", "煤气味")),
    (EventType.FIRE, ("火情", "着火", "失火", "明火", "冒烟")),
    (
        EventType.PERSONAL_SAFETY,
        ("有人被困", "人员受伤", "人身危险", "人员安全", "有人摔倒"),
    ),
    (
        EventType.EQUIPMENT_FAULT,
        ("设备故障", "设施故障", "设备隐患", "消防隐患", "护栏损坏", "井盖破损"),
    ),
)


def classify_security_event(description: str) -> tuple[EventType, EventRiskLevel]:
    """Return the stored event type and deterministic minimum risk level."""

    text = (description or "").strip()
    event_type = next(
        (candidate for candidate, cues in _EVENT_CUES if any(cue in text for cue in cues)),
        EventType.OTHER,
    )
    risk = (
        EventRiskLevel.HIGH_RISK
        if event_type in {EventType.GAS_LEAK, EventType.FIRE, EventType.PERSONAL_SAFETY}
        else EventRiskLevel.MEDIUM
    )
    return event_type, risk
