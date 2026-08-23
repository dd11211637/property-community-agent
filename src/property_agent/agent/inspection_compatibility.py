"""Explicit legacy graph projections for migrated inspection capabilities."""

from __future__ import annotations

from typing import Any

from property_agent.agent.working_state import (
    InspectionEventWorkingState,
    InspectionTaskWorkingState,
)
from property_agent.inspection.domain.classification import normalize_security_event
from property_agent.inspection.domain.enums import EventRiskLevel


def inspection_action(state: Any) -> str:
    domain = state.domain
    if isinstance(domain, (InspectionTaskWorkingState, InspectionEventWorkingState)):
        return str(domain.action or "").lower()
    return str(state.slots.get("action") or "").lower()


def project_inspection_context(provider: Any, projector: Any, state: Any) -> Any:
    platform_context = provider(state)
    return projector(platform_context) if projector is not None else platform_context


def apply_event_risk_floor(slots: dict[str, Any]) -> None:
    """Project shared deterministic normalization into legacy graph slots."""
    normalized = normalize_security_event(
        str(slots.get("description") or ""),
        slots.get("risk_level"),
    )
    slots["event_type"] = normalized.event_type.value
    slots["risk_level"] = normalized.risk_level.value
    if normalized.risk_level == EventRiskLevel.HIGH_RISK:
        slots["safety_notice"] = (
            "请优先远离危险区域，不要触碰可疑设备或明火；如存在即时人身危险，"
            "请立即联系当地紧急救援。确认上报后系统会同步通知值班人员。"
        )
