"""Mechanical telemetry projection for the lifecycle coordinator."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from property_agent.agent.application.accepted_head import cursor_for, publish_accepted, runtime_for
from property_agent.agent.telemetry_contracts import classify_turn


@dataclass(frozen=True, slots=True)
class TimedTurnSpan:
    delegate: Any
    started_at: float

    def set_attribute(self, key: str, value: Any) -> None:
        self.delegate.set_attribute(key, value)


@contextmanager
def observe_plan(observability: Any, plan: Any, operation: str, *, confirmed: bool = False):
    with observability.observe_turn(
        conversation_id=plan.state.conversation_id,
        run_id=plan.lease.run_id if plan.lease is not None else None,
        fence=plan.lease.fence if plan.lease is not None else None,
        expected_version=plan.expected_version,
        confirmed=confirmed,
        runtime_version=plan.runtime_version,
        operation=operation,
        request_id=getattr(plan.ctx, "request_id", None),
    ) as span:
        yield TimedTurnSpan(span, perf_counter())


def runtime_context(observability: Any, plan: Any, *, token: str | None = None):
    observation = observability.observation(
        runtime_version=plan.runtime_version,
        request_id=getattr(plan.ctx, "request_id", None),
    )
    return runtime_for(plan, token=token, observation=observation)


def accepted_cursor(observability: Any, checkpointer: Any, plan: Any):
    return cursor_for(
        checkpointer,
        plan.state.conversation_id,
        observability=observability,
        runtime_version=plan.runtime_version,
    )


def publish_result(observability: Any, checkpointer: Any, plan: Any, result: Any):
    return publish_accepted(
        checkpointer,
        plan.state.conversation_id,
        plan,
        result,
        observability=observability,
    )


def engine_span_name(plan: Any, action: str) -> str:
    return f"langgraph.{action}"


def finish_turn(observability: Any, plan: Any, turn: Any, operation: str, span: Any) -> None:
    outcome = classify_turn(turn).value
    span.set_attribute("agent.intent", turn.state.intent)
    span.set_attribute(
        "agent.degraded",
        observability.status()["state"] == "ENABLED_DEGRADED",
    )
    steps = getattr(getattr(turn.state, "plan", None), "steps", ())
    latency_class = "multi_step" if len(steps) > 1 else "simple"
    observability.duration(
        "agent_turn_duration_seconds",
        perf_counter() - span.started_at,
        attributes={
            "runtime": plan.runtime_version,
            "operation": operation,
            "outcome": outcome,
            "reason": latency_class,
        },
    )
    observability.count(
        "agent_outcome_total",
        attributes={
            "runtime": plan.runtime_version,
            "operation": operation,
            "outcome": outcome,
        },
    )


__all__ = [
    "accepted_cursor",
    "engine_span_name",
    "finish_turn",
    "observe_plan",
    "publish_result",
    "runtime_context",
]
