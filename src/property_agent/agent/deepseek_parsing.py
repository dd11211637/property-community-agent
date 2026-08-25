"""Deterministic parsing for the bounded DeepSeek analysis response."""

from __future__ import annotations

import json

from property_agent.agent.model_contracts import ModelAnalysis
from property_agent.agent.policies import Intent
from property_agent.agent.telemetry_contracts import model_schema_failure

_VALID_INTENTS = frozenset(intent.value for intent in Intent)


def parse_deepseek_analysis(content: str) -> ModelAnalysis:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise model_schema_failure("DeepSeek returned invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"intent", "confidence", "slots"}:
        raise model_schema_failure("DeepSeek JSON does not match the required schema")
    intent = value["intent"]
    confidence = value["confidence"]
    slots = value["slots"]
    if intent not in _VALID_INTENTS:
        raise model_schema_failure("DeepSeek returned an unsupported intent")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise model_schema_failure("DeepSeek returned an invalid confidence")
    if not 0 <= float(confidence) <= 1:
        raise model_schema_failure("DeepSeek confidence is outside [0, 1]")
    if not isinstance(slots, dict) or not all(isinstance(key, str) for key in slots):
        raise model_schema_failure("DeepSeek returned invalid slots")
    return ModelAnalysis(
        intent=intent,
        confidence=float(confidence),
        slots=slots,
        provider="deepseek",
    )


__all__ = ["parse_deepseek_analysis"]
