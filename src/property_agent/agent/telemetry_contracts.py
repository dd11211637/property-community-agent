"""Bounded outcome and error categories shared by telemetry and presentation."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from time import perf_counter
from typing import Any

import httpx

from property_agent.agent.model_contracts import ModelGatewayError


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


def model_failure_category(exc: BaseException) -> str:
    """Classify a provider failure without inspecting or exporting its message."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, httpx.TimeoutException):
            return "timeout"
        if isinstance(current, httpx.TransportError):
            return "transport_failure"
        if isinstance(current, httpx.HTTPStatusError):
            return "provider_failure"
        current = current.__cause__
    if isinstance(exc, ModelGatewayError):
        return exc.category
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "schema_failure"
    return "infrastructure_failure"


def model_schema_failure(message: str) -> ModelGatewayError:
    return ModelGatewayError(message, category="schema_failure")


def observe_model_provider_attempt(
    observe: Callable[[str, dict[str, Any]], None],
    operation: str,
    request: Callable[[], Any],
) -> Any:
    """Observe one DeepSeek physical request without retaining payload or error text."""
    fields = {"provider": "DeepSeek", "operation": operation}
    started = perf_counter()
    observe("model_provider_request", fields)
    try:
        result = request()
    except Exception as exc:
        observe(
            "model_provider_outcome",
            {
                **fields,
                "outcome": model_failure_category(exc),
                "duration_seconds": perf_counter() - started,
            },
        )
        raise
    observe(
        "model_provider_outcome",
        {**fields, "outcome": "success", "duration_seconds": perf_counter() - started},
    )
    return result


__all__ = [
    "AgentOutcome",
    "classify_turn",
    "model_failure_category",
    "model_schema_failure",
    "observe_model_provider_attempt",
]
