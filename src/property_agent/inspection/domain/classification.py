"""Deterministic security-event classification and minimum-risk rules."""

from __future__ import annotations

from dataclasses import dataclass

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
        (
            "设备故障",
            "设施故障",
            "设备隐患",
            "消防隐患",
            "护栏损坏",
            "井盖破损",
            "安全出口",
            "消防通道",
            "疏散通道",
            "杂物占道",
        ),
    ),
)


def classify_security_event(description: str) -> tuple[EventType, EventRiskLevel]:
    """Return the stored event type and deterministic minimum risk level."""

    text = (description or "").strip()
    event_type = next(
        (candidate for candidate, cues in _EVENT_CUES if any(cue in text for cue in cues)),
        EventType.OTHER,
    )
    high_risk = event_type in {
        EventType.GAS_LEAK,
        EventType.FIRE,
        EventType.PERSONAL_SAFETY,
    } or _severely_blocks_egress(text)
    risk = EventRiskLevel.HIGH_RISK if high_risk else EventRiskLevel.MEDIUM
    return event_type, risk


def _severely_blocks_egress(text: str) -> bool:
    egress = any(cue in text for cue in ("安全出口", "消防通道", "疏散通道", "出口"))
    blocked = any(
        cue in text
        for cue in (
            "不能通行",
            "无法通行",
            "没法通行",
            "完全堵住",
            "严重堵塞",
            "基本堵住",
            "堵了一大半",
            "几乎没法",
        )
    )
    return egress and blocked


@dataclass(frozen=True, slots=True)
class NormalizedSecurityEvent:
    event_type: EventType
    risk_level: EventRiskLevel


def normalize_security_event(
    description: str,
    requested_risk: str | EventRiskLevel | None = None,
) -> NormalizedSecurityEvent:
    """Derive event type and enforce a deterministic risk floor.

    Caller-controlled risk may raise the result, but can never lower the floor
    derived from the reported facts. Event type is always fact-derived.
    """
    event_type, minimum = classify_security_event(description)
    try:
        requested = EventRiskLevel(str(requested_risk).upper()) if requested_risk else minimum
    except ValueError:
        requested = minimum
    rank = {
        EventRiskLevel.LOW: 0,
        EventRiskLevel.MEDIUM: 1,
        EventRiskLevel.HIGH_RISK: 2,
    }
    effective = requested if rank[requested] > rank[minimum] else minimum
    return NormalizedSecurityEvent(event_type, effective)
