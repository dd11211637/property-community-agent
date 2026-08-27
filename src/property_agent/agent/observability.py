"""Bounded Agent tracing and SLO metrics built on application-owned OTel providers."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, Status, StatusCode, get_current_span

from property_agent.agent.runtime import RuntimeObservation
from property_agent.agent.telemetry_provider import TelemetryProviders, TelemetryState
from property_agent.config import Settings

_METRIC_LABELS = frozenset(
    {
        "runtime",
        "operation",
        "outcome",
        "reason",
        "specialist",
        "capability",
        "provider",
        "config_version",
        "salt_version",
        "eligibility_policy_version",
        "decision_class",
    }
)
_TRACE_TEXT_LIMIT = 128
_CERTIFICATION_CAMPAIGN = re.compile(r"[a-f0-9]{32}")
_CHAOS_CASE = re.compile(r"C(?:[1-9]|1[0-2])")
_TRACE_ATTRIBUTE_KEYS = frozenset(
    {
        "agent.conversation.id",
        "agent.runtime.version",
        "agent.operation",
        "agent.confirmed",
        "agent.run.id",
        "agent.lease.fence",
        "agent.checkpoint.expected_version",
        "http.request.id",
        "http.request.method",
        "service.version",
        "certification.campaign.id",
        "certification.chaos.case",
        "runtime",
        "operation",
        "outcome",
        "reason",
        "specialist",
        "capability",
        "provider",
    }
)


class Counter:
    def inc(self, amount: int = 1, attributes: Mapping[str, str] | None = None) -> None:
        raise NotImplementedError

    def value(self) -> int:
        raise NotImplementedError


class InMemoryCounter(Counter):
    def __init__(self, name: str, otel_counter: Any | None = None) -> None:
        self.name = name
        self._value = 0
        self._otel = otel_counter

    def inc(self, amount: int = 1, attributes: Mapping[str, str] | None = None) -> None:
        self._value += amount
        if self._otel is not None:
            try:
                self._otel.add(amount, dict(attributes or {}))
            except Exception:
                pass

    def value(self) -> int:
        return self._value


DualCounter = InMemoryCounter


@dataclass
class Metrics:
    conversation_busy: Counter
    checkpoint_conflict: Counter
    stale_fence_rejected: Counter
    approval_rollback: Counter


class NullSpan:
    def __init__(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.name = name
        self.attributes = dict(attributes or {})

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, _status: Any) -> None:
        return None

    def record_exception(self, _exception: BaseException, **_kwargs: Any) -> None:
        return None


class NullTracer:
    @contextmanager
    def start_as_current_span(
        self, name: str, attributes: dict[str, Any] | None = None, **_kwargs: Any
    ) -> Iterator[NullSpan]:
        yield NullSpan(name, attributes)


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    value: float
    attributes: dict[str, str]
    kind: str


@dataclass
class AgentObservability:
    metrics: Metrics
    tracer: Any
    meter: Any | None
    providers: TelemetryProviders
    enabled: bool
    degraded: bool
    release_sha: str = ""
    deployment_environment: str = ""
    points: list[MetricPoint] = field(default_factory=list)
    spans: list[NullSpan] = field(default_factory=list)
    _counters: dict[str, Any] = field(default_factory=dict)
    _histograms: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def in_memory(cls, *, state: TelemetryState = TelemetryState.ENABLED_DEGRADED):
        providers = TelemetryProviders.local(state)
        return cls._assemble(providers, release_sha="", environment="test")

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        span_exporter: Any | None = None,
        metric_exporter: Any | None = None,
    ):
        providers = TelemetryProviders.build(
            settings, span_exporter=span_exporter, metric_exporter=metric_exporter
        )
        return cls._assemble(
            providers,
            release_sha=settings.release_sha.strip(),
            environment=settings.deployment_environment or settings.env,
        )

    @classmethod
    def _assemble(cls, providers, *, release_sha: str, environment: str):
        status = providers.health.snapshot()
        tracer = (
            providers.tracer_provider.get_tracer("property-agent")
            if providers.tracer_provider is not None
            else NullTracer()
        )
        meter = (
            providers.meter_provider.get_meter("property-agent")
            if providers.meter_provider is not None
            else None
        )
        legacy_names = (
            "agent_conversation_busy_total",
            "agent_checkpoint_conflict_total",
            "agent_stale_fence_rejected_total",
            "agent_approval_rollback_total",
        )
        counters = [
            InMemoryCounter(name, meter.create_counter(name) if meter is not None else None)
            for name in legacy_names
        ]
        return cls(
            Metrics(*counters),
            tracer,
            meter,
            providers,
            enabled=status.state is not TelemetryState.DISABLED,
            degraded=status.state is TelemetryState.ENABLED_DEGRADED,
            release_sha=release_sha,
            deployment_environment=environment,
        )

    def status(self) -> dict[str, Any]:
        status = self.providers.health.snapshot()
        self.degraded = status.state is TelemetryState.ENABLED_DEGRADED
        return status.public_dict()

    @staticmethod
    def metric_attributes(attributes: Mapping[str, Any] | None) -> dict[str, str]:
        return {
            key: str(value)[:64]
            for key, value in dict(attributes or {}).items()
            if key in _METRIC_LABELS and value is not None
        }

    def count(
        self, name: str, *, amount: int = 1, attributes: Mapping[str, Any] | None = None
    ) -> None:
        safe = self.metric_attributes(attributes)
        self.points.append(MetricPoint(name, float(amount), safe, "counter"))
        if self.meter is None:
            return
        instrument = self._counters.get(name)
        if instrument is None:
            instrument = self.meter.create_counter(name)
            self._counters[name] = instrument
        try:
            instrument.add(amount, safe)
        except Exception:
            self.providers.health.failed("metric_record_exception")

    def duration(
        self, name: str, seconds: float, *, attributes: Mapping[str, Any] | None = None
    ) -> None:
        safe = self.metric_attributes(attributes)
        self.points.append(MetricPoint(name, float(seconds), safe, "histogram"))
        if self.meter is None:
            return
        instrument = self._histograms.get(name)
        if instrument is None:
            instrument = self.meter.create_histogram(name, unit="s")
            self._histograms[name] = instrument
        try:
            instrument.record(seconds, safe)
        except Exception:
            self.providers.health.failed("metric_record_exception")

    def value(
        self, name: str, value: float, *, attributes: Mapping[str, Any] | None = None
    ) -> None:
        safe = self.metric_attributes(attributes)
        self.points.append(MetricPoint(name, float(value), safe, "histogram"))
        if self.meter is None:
            return
        instrument = self._histograms.get(name)
        if instrument is None:
            instrument = self.meter.create_histogram(name, unit="1")
            self._histograms[name] = instrument
        try:
            instrument.record(value, safe)
        except Exception:
            self.providers.health.failed("metric_record_exception")

    @contextmanager
    def span(self, name: str, *, attributes: Mapping[str, Any] | None = None):
        values = dict(attributes or {})
        campaign_id = os.getenv("PR7B_CHAOS_CAMPAIGN_ID", "").strip()
        if _CERTIFICATION_CAMPAIGN.fullmatch(campaign_id):
            values["certification.campaign.id"] = campaign_id
        chaos_case = os.getenv("PR7B_CHAOS_CASE_ID", "").strip()
        if _CHAOS_CASE.fullmatch(chaos_case):
            values["certification.chaos.case"] = chaos_case
        safe = self._trace_attributes(values)
        started = perf_counter()
        with self.tracer.start_as_current_span(
            name, attributes=safe, kind=SpanKind.INTERNAL
        ) as span:
            if isinstance(span, NullSpan):
                self.spans.append(span)
            try:
                yield span
            except Exception as exc:
                span.set_attribute("error.category", type(exc).__name__[:64])
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__[:64]))
                raise
            finally:
                self.duration(
                    "agent_boundary_duration_seconds",
                    perf_counter() - started,
                    attributes={"operation": name},
                )

    @contextmanager
    def request_span(self, carrier: Mapping[str, str], *, request_id: str, method: str):
        attributes = {"http.request.id": request_id, "http.request.method": method}
        with self.tracer.start_as_current_span(
            "http.request",
            context=extract(carrier),
            attributes=self._trace_attributes(attributes),
            kind=SpanKind.SERVER,
        ) as span:
            if isinstance(span, NullSpan):
                self.spans.append(span)
            yield span

    @staticmethod
    def correlation() -> tuple[str | None, str | None]:
        context = get_current_span().get_span_context()
        if not context.is_valid:
            return None, None
        return f"{context.trace_id:032x}", f"{context.span_id:016x}"

    @contextmanager
    def observe_turn(
        self,
        *,
        conversation_id: str,
        run_id: Any | None = None,
        fence: int | None = None,
        expected_version: int | None = None,
        confirmed: bool = False,
        runtime_version: str = "v1",
        operation: str = "start",
        request_id: str | None = None,
    ):
        attributes = {
            "agent.conversation.id": conversation_id,
            "agent.runtime.version": runtime_version,
            "agent.operation": operation,
            "agent.confirmed": confirmed,
            "agent.run.id": run_id,
            "agent.lease.fence": fence,
            "agent.checkpoint.expected_version": expected_version,
            "http.request.id": request_id,
            "service.version": self.release_sha or None,
        }
        self.count(
            "agent_request_total",
            attributes={"runtime": runtime_version, "operation": operation},
        )
        with self.span("agent.turn", attributes=attributes) as span:
            yield span

    def observation(self, *, runtime_version: str, request_id: str | None) -> RuntimeObservation:
        context = get_current_span().get_span_context()
        trace_id = f"{context.trace_id:032x}" if context.is_valid else None
        span_id = f"{context.span_id:016x}" if context.is_valid else None
        return RuntimeObservation(
            trace_id=trace_id,
            span_id=span_id,
            request_id=request_id,
            runtime_version=runtime_version,
            release_sha=self.release_sha or None,
        )

    def observe_runtime_assignment(self, assignment: Any) -> None:
        """Record bounded PR7-C assignment facts without identity, prompts, or salt."""
        self.count(
            "agent_runtime_assignment_total",
            attributes={
                "runtime": assignment.runtime_version,
                "reason": assignment.eligibility_reason.value,
                "config_version": assignment.config_version,
                "salt_version": assignment.salt_version,
                "eligibility_policy_version": assignment.eligibility_policy_version,
                "decision_class": assignment.decision_class.value,
            },
        )

    def shutdown(self) -> None:
        self.providers.shutdown()

    @staticmethod
    def _trace_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
        safe = {}
        for key, value in dict(attributes or {}).items():
            if value is None or key not in _TRACE_ATTRIBUTE_KEYS:
                continue
            safe[str(key)[:96]] = (
                str(value)[:_TRACE_TEXT_LIMIT] if isinstance(value, (str, bytes)) else value
            )
        return safe


__all__ = [
    "AgentObservability",
    "Counter",
    "DualCounter",
    "InMemoryCounter",
    "MetricPoint",
    "Metrics",
    "NullSpan",
    "NullTracer",
]
