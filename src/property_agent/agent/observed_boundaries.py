"""Small non-authoritative telemetry adapters for model and Memory ownership boundaries."""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import Any

from property_agent.agent.telemetry_contracts import model_failure_category
from property_agent.platform.application.approval_service import ApprovalError

_MODEL_METHODS = frozenset(
    {
        "analyze",
        "analyze_with_context",
        "propose_plan",
        "extract_candidates",
        "judge_relevance",
        "classify_intent",
        "extract_slots",
        "draft_announcement",
        "revise_announcement",
        "plan_read",
    }
)


def _model_failure(exc: BaseException) -> str:
    return model_failure_category(exc)


class ObservedModelGateway:
    def __init__(self, delegate: Any, observability: Any) -> None:
        self._delegate = delegate
        self._telemetry = observability
        self._provider = type(delegate).__name__

    def __getattr__(self, name: str):
        attribute = getattr(self._delegate, name)
        if name not in _MODEL_METHODS or not callable(attribute):
            return attribute

        def observed(*args, **kwargs):
            return self._invoke(name, attribute, args, kwargs)

        return observed

    def _invoke(self, operation: str, method: Any, args: tuple, kwargs: dict):
        attributes = {"operation": operation}
        started = perf_counter()
        self._telemetry.count("agent_model_operation_request_total", attributes=attributes)
        try:
            with self._telemetry.span("model.request", attributes=attributes):
                result = method(*args, **kwargs)
        except Exception as exc:
            outcome = _model_failure(exc)
            self._telemetry.count(
                "agent_model_operation_outcome_total",
                attributes={**attributes, "outcome": outcome},
            )
            self._telemetry.duration(
                "agent_model_operation_duration_seconds",
                perf_counter() - started,
                attributes=attributes,
            )
            raise
        self._telemetry.count(
            "agent_model_operation_outcome_total",
            attributes={
                **attributes,
                "outcome": "degraded_success" if getattr(result, "degraded", False) else "success",
                "reason": "degraded" if getattr(result, "degraded", False) else "primary",
            },
        )
        self._telemetry.duration(
            "agent_model_operation_duration_seconds",
            perf_counter() - started,
            attributes=attributes,
        )
        return result


class ObservedPlanner:
    def __init__(self, delegate: Any, observability: Any) -> None:
        self._delegate = delegate
        self._telemetry = observability

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def create_plan(self, state: Any, runtime: Any):
        with self._telemetry.span("supervisor.plan", attributes={}):
            return self._delegate.create_plan(state, runtime)

    def revalidate_memories(self, state: Any, runtime: Any) -> None:
        with self._telemetry.span("supervisor.memory_revalidate", attributes={}):
            self._delegate.revalidate_memories(state, runtime)


class ObservedSpecialist:
    def __init__(self, delegate: Any, observability: Any) -> None:
        self._delegate = delegate
        self._telemetry = observability
        self.name = delegate.name

    def invoke(self, step: Any, state: Any, runtime: Any, prior_results: Any):
        attributes = {
            "specialist": self.name.value,
            "capability": step.capability,
        }
        with self._telemetry.span("specialist.execute", attributes=attributes):
            return self._delegate.invoke(step, state, runtime, prior_results)


class ObservedApprovalService:
    def __init__(self, delegate: Any, observability: Any) -> None:
        self._delegate = delegate
        self._telemetry = observability

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def create_pending(self, **kwargs):
        return self._invoke("request", self._delegate.create_pending, kwargs)

    def approve(self, **kwargs):
        return self._invoke("approve", self._delegate.approve, kwargs)

    def consume(self, **kwargs):
        return self._invoke("consume", self._delegate.consume, kwargs)

    def _invoke(self, operation: str, method: Any, kwargs: dict[str, Any]):
        started = perf_counter()
        try:
            with self._telemetry.span(f"approval.{operation}", attributes={}):
                result = method(**kwargs)
        except ApprovalError as exc:
            binding = exc.code.endswith("MISMATCH") or exc.code == "APPROVAL_PARAMS_CHANGED"
            outcome = "binding_rejected" if binding else "rejected"
            self._record(operation, outcome, exc.code, started)
            raise
        except Exception:
            self._record(operation, "failed", "infrastructure_failure", started)
            raise
        self._record(operation, "success", None, started)
        return result

    def _record(self, operation: str, outcome: str, reason: str | None, started: float) -> None:
        attributes = {"operation": operation, "outcome": outcome, "reason": reason}
        self._telemetry.count("agent_approval_operation_total", attributes=attributes)
        self._telemetry.duration(
            "agent_approval_operation_duration_seconds",
            perf_counter() - started,
            attributes=attributes,
        )


class ObservedMemoryService:
    """Observe optional maintenance calls without changing canonical Memory ownership."""

    def __init__(self, delegate: Any, observability: Any) -> None:
        self._delegate = delegate
        self._telemetry = observability

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def reindex_memories(self, *, limit: int = 100):
        started = perf_counter()
        try:
            with self._telemetry.span("memory.reindex", attributes={"operation": "reindex"}):
                result = self._delegate.reindex_memories(limit=limit)
        except Exception:
            self._record("failed", "infrastructure_failure", started, 0)
            raise
        outcome = "degraded" if result.degraded or result.failed else "success"
        self._record(outcome, None, started, result.remaining)
        return result

    def _record(self, outcome: str, reason: str | None, started: float, remaining: int) -> None:
        attributes = {"operation": "reindex", "outcome": outcome, "reason": reason}
        self._telemetry.count("agent_memory_reindex_total", attributes=attributes)
        self._telemetry.duration(
            "agent_memory_reindex_duration_seconds",
            perf_counter() - started,
            attributes=attributes,
        )
        self._telemetry.value(
            "agent_memory_reindex_backlog", float(remaining), attributes={"operation": "reindex"}
        )


class ObservedMemoryReader:
    def __init__(self, delegate: Any, observability: Any) -> None:
        self._delegate = delegate
        self._telemetry = observability

    def __call__(self, text: str, runtime: Any):
        return self._invoke("retrieve", self._delegate, text, runtime)

    def revalidate(self, text: str, runtime: Any, previous: Any):
        return self._invoke("revalidate", self._delegate.revalidate, text, runtime, previous)

    def _invoke(self, operation: str, method: Any, *args):
        started = perf_counter()
        attributes = {"operation": operation, "runtime": args[1].observation.runtime_version}
        try:
            with self._telemetry.span("memory.retrieve", attributes=attributes):
                result = method(*args)
        except Exception:
            self._telemetry.count(
                "agent_memory_retrieve_total",
                attributes={
                    **attributes,
                    "outcome": "degraded",
                    "reason": "retrieve_failure",
                },
            )
            self._telemetry.duration(
                "agent_memory_retrieve_duration_seconds",
                perf_counter() - started,
                attributes=attributes,
            )
            raise
        self._telemetry.count(
            "agent_memory_retrieve_total",
            attributes={
                **attributes,
                "outcome": "degraded" if getattr(result, "degraded", False) else "success",
                "reason": getattr(result, "degraded_reason", None),
            },
        )
        self._telemetry.duration(
            "agent_memory_retrieve_duration_seconds",
            perf_counter() - started,
            attributes=attributes,
        )
        self._telemetry.value(
            "agent_memory_result_count",
            float(len(getattr(result, "items", ()))),
            attributes={"operation": operation},
        )
        return result


class ObservedMemoryWriter:
    def __init__(self, delegate: Any, observability: Any) -> None:
        self._delegate = delegate
        self._telemetry = observability

    def write_accepted_turn(self, **kwargs):
        started = perf_counter()
        attributes = {"operation": "write"}
        try:
            with self._telemetry.span("memory.write", attributes=attributes):
                result = self._delegate.write_accepted_turn(**kwargs)
        except Exception:
            self._telemetry.count(
                "agent_memory_writer_total",
                attributes={**attributes, "outcome": "failed", "reason": "writer_failure"},
            )
            self._telemetry.duration(
                "agent_memory_writer_duration_seconds",
                perf_counter() - started,
                attributes=attributes,
            )
            raise
        self._telemetry.count(
            "agent_memory_writer_total",
            attributes={
                **attributes,
                "outcome": "degraded" if result.degraded else "success",
            },
        )
        self._telemetry.duration(
            "agent_memory_writer_duration_seconds",
            perf_counter() - started,
            attributes=attributes,
        )
        return result


def observe_memory_runtime(runtime: Any, observability: Any):
    return replace(
        runtime,
        reader=ObservedMemoryReader(runtime.reader, observability),
        writer=ObservedMemoryWriter(runtime.writer, observability),
    )


def capability_observer(observability: Any):
    def observe(event: str, fields: dict[str, Any]) -> None:
        if event not in {"capability_finished", "capability_failed"}:
            return
        attributes = {
            "capability": fields.get("capability"),
            "outcome": fields.get("outcome"),
            "reason": fields.get("reason"),
        }
        observability.count("agent_capability_request_total", attributes=attributes)
        observability.duration(
            "agent_capability_duration_seconds",
            float(fields.get("duration_seconds") or 0.0),
            attributes=attributes,
        )

    return observe


def model_provider_observer(observability: Any):
    def observe(event: str, fields: dict[str, Any]) -> None:
        attributes = {
            "provider": fields.get("provider"),
            "operation": fields.get("operation"),
        }
        if event == "model_provider_request":
            observability.count("agent_model_provider_request_total", attributes=attributes)
        elif event == "model_provider_outcome":
            outcome_attributes = {**attributes, "outcome": fields.get("outcome")}
            observability.count("agent_model_provider_outcome_total", attributes=outcome_attributes)
            observability.duration(
                "agent_model_provider_duration_seconds",
                float(fields.get("duration_seconds") or 0.0),
                attributes=outcome_attributes,
            )
        if event == "model_retry":
            observability.count(
                "agent_model_retry_total",
                attributes={**attributes, "reason": "retryable"},
            )
        elif event == "model_fallback":
            observability.count(
                "agent_model_fallback_total",
                attributes={**attributes, "outcome": fields.get("outcome")},
            )

    return observe


def supervisor_observer(observability: Any):
    def observe(event: str, fields: dict[str, Any]) -> None:
        if event == "supervisor_plan_created":
            observability.count(
                "agent_plan_shape_total",
                attributes={
                    "runtime": fields.get("runtime"),
                    "operation": (
                        "multi_step" if int(fields.get("step_count") or 0) > 1 else "single_step"
                    ),
                    "reason": fields.get("classification"),
                },
            )
        mapping = {
            "supervisor_plan_created": "plan_created",
            "specialist_delegated": "delegated",
            "specialist_completed": "completed",
        }
        operation = mapping.get(event)
        if operation is None:
            return
        observability.count(
            "agent_orchestration_total",
            attributes={
                "operation": operation,
                "specialist": fields.get("specialist"),
                "capability": fields.get("capability"),
                "outcome": fields.get("outcome"),
                "reason": fields.get("reason"),
            },
        )

    return observe


__all__ = [
    "ObservedMemoryReader",
    "ObservedMemoryService",
    "ObservedMemoryWriter",
    "ObservedModelGateway",
    "ObservedApprovalService",
    "ObservedPlanner",
    "ObservedSpecialist",
    "capability_observer",
    "model_provider_observer",
    "observe_memory_runtime",
    "supervisor_observer",
]
