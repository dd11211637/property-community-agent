"""Application-owned OpenTelemetry providers and exporter health.

Providers are deliberately not installed as process globals.  A FastAPI application owns
one instance, flushes it during shutdown, and can build repeated test applications without
cross-test instruments or "provider already set" warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExportResult, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult


class TelemetryState(StrEnum):
    DISABLED = "DISABLED"
    ENABLED_HEALTHY = "ENABLED_HEALTHY"
    ENABLED_DEGRADED = "ENABLED_DEGRADED"


@dataclass(frozen=True, slots=True)
class TelemetryStatus:
    state: TelemetryState
    configured: bool
    provider_created: bool
    exporter_configured: bool
    last_export_failure_category: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "configured": self.configured,
            "provider_created": self.provider_created,
            "exporter_configured": self.exporter_configured,
            "last_export_failure_category": self.last_export_failure_category,
        }


class ExportHealth:
    """Thread-safe bounded exporter status; never stores endpoint or payload data."""

    def __init__(self, initial: TelemetryStatus) -> None:
        self._status = initial
        self._lock = Lock()

    def snapshot(self) -> TelemetryStatus:
        with self._lock:
            return self._status

    def failed(self, category: str) -> None:
        with self._lock:
            self._status = TelemetryStatus(
                TelemetryState.ENABLED_DEGRADED,
                configured=True,
                provider_created=True,
                exporter_configured=True,
                last_export_failure_category=category[:64],
            )

    def succeeded(self) -> None:
        with self._lock:
            if self._status.state is TelemetryState.ENABLED_DEGRADED:
                return
            self._status = TelemetryStatus(
                TelemetryState.ENABLED_HEALTHY,
                configured=True,
                provider_created=True,
                exporter_configured=True,
            )


class TrackingSpanExporter:
    def __init__(self, delegate: Any, health: ExportHealth) -> None:
        self._delegate = delegate
        self._health = health

    def export(self, spans) -> SpanExportResult:
        try:
            result = self._delegate.export(spans)
        except Exception:
            self._health.failed("trace_export_exception")
            return SpanExportResult.FAILURE
        if result is SpanExportResult.SUCCESS:
            self._health.succeeded()
        else:
            self._health.failed("trace_export_failure")
        return result

    def shutdown(self, *args, **kwargs):
        return self._delegate.shutdown(*args, **kwargs)

    def force_flush(self, *args, **kwargs):
        method = getattr(self._delegate, "force_flush", None)
        return True if method is None else method(*args, **kwargs)


class TrackingMetricExporter:
    def __init__(self, delegate: Any, health: ExportHealth) -> None:
        self._delegate = delegate
        self._health = health
        self._preferred_temporality = delegate._preferred_temporality
        self._preferred_aggregation = delegate._preferred_aggregation

    def export(self, metrics_data, timeout_millis=10_000, **kwargs) -> MetricExportResult:
        try:
            result = self._delegate.export(metrics_data, timeout_millis, **kwargs)
        except Exception:
            self._health.failed("metric_export_exception")
            return MetricExportResult.FAILURE
        if result is MetricExportResult.SUCCESS:
            self._health.succeeded()
        else:
            self._health.failed("metric_export_failure")
        return result

    def shutdown(self, *args, **kwargs):
        return self._delegate.shutdown(*args, **kwargs)

    def force_flush(self, *args, **kwargs):
        method = getattr(self._delegate, "force_flush", None)
        return True if method is None else method(*args, **kwargs)


class TelemetryProviders:
    """Own one trace provider, one meter provider, and their background workers."""

    def __init__(self, tracer_provider, meter_provider, health: ExportHealth) -> None:
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider
        self.health = health
        self._closed = False

    @classmethod
    def local(cls, state: TelemetryState = TelemetryState.ENABLED_DEGRADED):
        status = TelemetryStatus(
            state,
            configured=state is not TelemetryState.DISABLED,
            provider_created=False,
            exporter_configured=False,
            last_export_failure_category=(
                "in_memory_telemetry" if state is TelemetryState.ENABLED_DEGRADED else None
            ),
        )
        return cls(None, None, ExportHealth(status))

    @classmethod
    def build(
        cls,
        settings: Any,
        *,
        span_exporter: Any | None = None,
        metric_exporter: Any | None = None,
    ) -> TelemetryProviders:
        if not settings.otel_enabled:
            health = ExportHealth(TelemetryStatus(TelemetryState.DISABLED, False, False, False))
            return cls(None, None, health)
        endpoint = settings.otel_exporter_endpoint.strip().rstrip("/")
        if not endpoint and (span_exporter is None or metric_exporter is None):
            status = TelemetryStatus(
                TelemetryState.ENABLED_DEGRADED,
                configured=True,
                provider_created=False,
                exporter_configured=False,
                last_export_failure_category="exporter_endpoint_missing",
            )
            return cls(None, None, ExportHealth(status))
        health = ExportHealth(TelemetryStatus(TelemetryState.ENABLED_HEALTHY, True, True, True))
        resource = Resource.create(cls._resource_attributes(settings))
        trace_exporter = span_exporter or OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        metrics_exporter = metric_exporter or OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(TrackingSpanExporter(trace_exporter, health))
        )
        reader = PeriodicExportingMetricReader(
            TrackingMetricExporter(metrics_exporter, health),
            export_interval_millis=settings.otel_export_interval_ms,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=(reader,))
        return cls(tracer_provider, meter_provider, health)

    @staticmethod
    def _resource_attributes(settings: Any) -> dict[str, str]:
        attributes = {
            "service.name": settings.otel_service_name or "property-agent",
            "deployment.environment.name": settings.deployment_environment or settings.env,
        }
        if settings.release_sha.strip():
            attributes["service.version"] = settings.release_sha.strip()
        return attributes

    def force_flush(self, timeout_millis: int = 5_000) -> bool:
        results = []
        for provider in (self.tracer_provider, self.meter_provider):
            if provider is not None:
                try:
                    results.append(bool(provider.force_flush(timeout_millis=timeout_millis)))
                except Exception:
                    self.health.failed("force_flush_exception")
                    results.append(False)
        return all(results) if results else True

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.force_flush()
        for provider in (self.tracer_provider, self.meter_provider):
            if provider is not None:
                try:
                    provider.shutdown()
                except Exception:
                    self.health.failed("shutdown_exception")


__all__ = ["TelemetryProviders", "TelemetryState", "TelemetryStatus"]
