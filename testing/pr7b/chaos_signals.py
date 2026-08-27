"""Causal production-signal contracts for PR7-B chaos drills."""

from __future__ import annotations

from typing import Any

# Each drill requires every listed production signal. Attribute predicates are
# intentionally low-cardinality and distinguish one fault outcome from another.
CHAOS_SIGNAL_CONTRACTS: dict[str, tuple[tuple[str, dict[str, str]], ...]] = {
    "C1": (("agent_model_provider_outcome_total", {"outcome": "timeout"}),),
    "C2": (("agent_model_provider_outcome_total", {"outcome": "schema_failure"}),),
    "C3": (("agent_memory_retrieve_total", {"outcome": "degraded"}),),
    "C4": (("database_pool_connection_failure_total", {}),),
    "C5": (("agent_checkpoint_persist_total", {"outcome": "FAILED_INFRASTRUCTURE"}),),
    "C6": (
        ("agent_accepted_head_publish_total", {"outcome": "FAILED_INFRASTRUCTURE"}),
        ("agent_accepted_head_orphan_total", {"reason": "publish_failure"}),
    ),
    "C7": (("agent_exact_cursor_resolution_total", {"outcome": "FOUND"}),),
    "C8": (("agent_exact_cursor_resolution_total", {"outcome": "FOUND"}),),
    "C9": (("agent_approval_operation_total", {"operation": "consume"}),),
    "C10": (("agent_memory_writer_total", {"outcome": "degraded"}),),
    "C11": (("agent_approval_operation_total", {"operation": "consume"}),),
    "C12": (
        ("agent_lease_operation_total", {"operation": "renew", "outcome": "lost"}),
        ("agent_stale_fence_rejected_total", {}),
    ),
}


def matching_signals(case_id: str, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one production-origin match for every required case signal."""
    matches: list[dict[str, Any]] = []
    for name, expected in CHAOS_SIGNAL_CONTRACTS.get(case_id, ()):
        match = next(
            (
                signal
                for signal in signals
                if signal.get("name") == name
                and signal.get("production_origin") is True
                and all(
                    signal.get("attributes", {}).get(key) == value
                    for key, value in expected.items()
                )
            ),
            None,
        )
        if match is None:
            return []
        matches.append(match)
    return matches


__all__ = ["CHAOS_SIGNAL_CONTRACTS", "matching_signals"]
