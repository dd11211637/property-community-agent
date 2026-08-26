"""Versioned R0 preproduction capacity target and safety bounds."""

from __future__ import annotations

from dataclasses import dataclass

R0_PROFILE_VERSION = "pr7b-r0-v1"
DEFAULT_R0_CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class CapacityBounds:
    expected_concurrency: int
    max_concurrency: int = 32
    max_conversations: int = 256
    max_requests: int = 100_000
    max_write_operations: int = 128
    max_run_seconds: int = 2_700
    request_timeout_seconds: float = 20.0
    infrastructure_failure_abort_rate: float = 0.05

    def validate(self) -> None:
        if not 1 <= self.expected_concurrency <= self.max_concurrency:
            raise ValueError("expected concurrency must be within the bounded admission limit")
        if self.max_conversations <= 0 or self.max_requests <= 0 or self.max_write_operations < 0:
            raise ValueError("conversation and write bounds must be non-negative")
        if self.max_conversations < self.expected_concurrency * 2 + 1:
            raise ValueError("conversation bound must cover the 2x spike workers and shared case")
        if not 1 <= self.max_run_seconds <= 2_700:
            raise ValueError("run duration must be between 1 and 2700 seconds")
        if not 0 < self.request_timeout_seconds <= 60:
            raise ValueError("request timeout must be between 0 and 60 seconds")
        if not 0 < self.infrastructure_failure_abort_rate <= 1:
            raise ValueError("abort rate must be between 0 and 1")


def r0_metadata(expected_concurrency: int) -> dict[str, int | float | str]:
    return {
        "profile_version": R0_PROFILE_VERSION,
        "expected_concurrency": expected_concurrency,
        "compose_backend_workers": 1,
        "stream_producer_admission": 16,
        "stream_shutdown_grace_seconds": 15.0,
        "model_total_deadline_seconds": 6.0,
        "agent_lease_seconds": 30,
        "database_pool_policy": "sqlalchemy-framework-defaults",
    }
