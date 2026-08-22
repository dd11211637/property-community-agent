"""Read-only compatibility projections from canonical Registry metadata."""

from __future__ import annotations

from pydantic_core import PydanticUndefined

from property_agent.agent.capabilities.catalog import default_capability_registry

CONTROLLED_READ_GUARD_MAPPING = {
    "untrusted_argument_guards": "retained legacy + typed input extra=forbid",
    "tool_allowlist": "retained legacy + CapabilityPolicy",
    "required_and_supported_arguments": "retained legacy + typed input validation",
    "argument_value_bounds": "retained legacy + typed input validation",
    "trusted_scope_output_check": "retained legacy",
    "max_steps": "retained legacy + CapabilityPolicy",
    "deadline": "retained legacy + CapabilityPolicy",
    "duplicate_fingerprint": "retained legacy + CapabilityPolicy",
    "result_record_bounds": "retained legacy",
    "hashed_trace_without_raw_arguments": "retained legacy",
    "provider_error_normalization": "retained legacy + CapabilityExecutor",
}


def migrated_tool_levels() -> dict[str, str]:
    return {
        spec.name: spec.baseline_risk.value for spec in default_capability_registry().inventory()
    }


def migrated_tool_slots() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    internal_fields = {"confirmation_token", "approval_ref", "idempotency_key"}
    for spec in default_capability_registry().inventory():
        result[spec.name] = [
            name
            for name, field in spec.input_type.model_fields.items()
            if field.default is PydanticUndefined
            and field.default_factory is None
            and name not in internal_fields
        ]
    return result


def migrated_presentation() -> dict[str, dict[str, str | None]]:
    return {
        spec.name: {
            "title": spec.presentation.title,
            "confirmation_title": spec.presentation.confirmation_title,
        }
        for spec in default_capability_registry().inventory()
    }
