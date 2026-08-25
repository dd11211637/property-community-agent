"""Bounded outcome and error categories shared by telemetry and presentation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class AgentOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    WAITING_CONFIRM = "WAITING_CONFIRM"
    HANDOVER = "HANDOVER"
    POLICY_DENIED = "POLICY_DENIED"
    BUSINESS_REJECTED = "BUSINESS_REJECTED"
    FAILED_INFRASTRUCTURE = "FAILED_INFRASTRUCTURE"


def classify_turn(turn: Any) -> AgentOutcome:
    state = turn.state
    if turn.awaiting_confirmation:
        return AgentOutcome.WAITING_CONFIRM
    if bool(state.handover_required):
        return AgentOutcome.HANDOVER
    if state.missing_slots or state.requested_slot:
        return AgentOutcome.NEEDS_CLARIFICATION
    error = state.error if isinstance(state.error, dict) else {}
    kind = str(error.get("kind") or "").lower()
    code = str(error.get("code") or "").upper()
    if kind == "policy" or code in {"POLICY_DENIED", "HUMAN_ONLY"}:
        return AgentOutcome.POLICY_DENIED
    if kind in {"business", "domain"}:
        return AgentOutcome.BUSINESS_REJECTED
    if error:
        return AgentOutcome.FAILED_INFRASTRUCTURE
    return AgentOutcome.COMPLETED


__all__ = ["AgentOutcome", "classify_turn"]
