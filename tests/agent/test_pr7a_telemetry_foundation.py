"""PR7-A production provider, exporter-health, and cardinality contracts."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from property_agent.agent.application.accepted_head import publish_accepted
from property_agent.agent.application.graph_engine import GraphExecutionResult
from property_agent.agent.observability import AgentObservability
from property_agent.agent.observed_boundaries import (
    ObservedMemoryService,
    ObservedModelGateway,
)
from property_agent.config import Settings


class RecordingMetricExporter(MetricExporter):
    def __init__(self, result=MetricExportResult.SUCCESS) -> None:
        super().__init__()
        self.result = result
        self.exports = []

    def export(self, metrics_data, timeout_millis=10_000, **_kwargs):
        self.exports.append(metrics_data)
        return self.result

    def shutdown(self, timeout_millis=30_000, **_kwargs):
        return None

    def force_flush(self, timeout_millis=10_000):
        return True


class FailingSpanExporter(InMemorySpanExporter):
    def export(self, spans):
        del spans
        return SpanExportResult.FAILURE


def _settings(**updates):
    return Settings(
        otel_enabled=True,
        otel_exporter_endpoint="http://collector.invalid:4318",
        otel_export_interval_ms=600_000,
        release_sha="abc123",
        deployment_environment="test",
        **updates,
    )


def test_private_providers_export_span_and_metric_without_global_install():
    spans = InMemorySpanExporter()
    metrics = RecordingMetricExporter()
    observation = AgentObservability.build(
        _settings(), span_exporter=spans, metric_exporter=metrics
    )
    try:
        with observation.observe_turn(
            conversation_id="conv-private",
            runtime_version="v2",
            operation="start",
        ):
            observation.count(
                "agent_test_total", attributes={"runtime": "v2", "outcome": "COMPLETED"}
            )
        assert observation.providers.force_flush()
        exported = spans.get_finished_spans()
        assert [span.name for span in exported] == ["agent.turn"]
        assert exported[0].attributes["agent.runtime.version"] == "v2"
        assert metrics.exports
        assert observation.status()["state"] == "ENABLED_HEALTHY"
    finally:
        observation.shutdown()


def test_export_failure_is_degraded_and_does_not_raise_into_business_path():
    observation = AgentObservability.build(
        _settings(),
        span_exporter=FailingSpanExporter(),
        metric_exporter=RecordingMetricExporter(),
    )
    try:
        with observation.span("business.application_service"):
            business_result = "committed"
        observation.providers.force_flush()
        assert business_result == "committed"
        status = observation.status()
        assert status["state"] == "ENABLED_DEGRADED"
        assert status["last_export_failure_category"] == "trace_export_failure"
    finally:
        observation.shutdown()


def test_missing_endpoint_and_disabled_modes_are_explicit():
    degraded = AgentObservability.build(Settings(otel_enabled=True, otel_exporter_endpoint=""))
    disabled = AgentObservability.build(Settings(otel_enabled=False))
    assert degraded.status() == {
        "state": "ENABLED_DEGRADED",
        "configured": True,
        "provider_created": False,
        "exporter_configured": False,
        "last_export_failure_category": "exporter_endpoint_missing",
    }
    assert disabled.status()["state"] == "DISABLED"


def test_metric_attributes_drop_high_cardinality_and_sensitive_dimensions():
    observation = AgentObservability.in_memory()
    observation.count(
        "agent_privacy_test_total",
        attributes={
            "runtime": "v1",
            "outcome": "COMPLETED",
            "conversation_id": "conv-secret",
            "run_id": "run-secret",
            "actor_id": "actor-secret",
            "house_id": "house-secret",
            "confirmation_token": "token-secret",
            "idempotency_key": "idem-secret",
        },
    )
    assert observation.points[-1].attributes == {
        "runtime": "v1",
        "outcome": "COMPLETED",
    }


def test_w3c_parent_reconstructs_runtime_observation_without_business_authority():
    spans = InMemorySpanExporter()
    observation = AgentObservability.build(
        _settings(), span_exporter=spans, metric_exporter=RecordingMetricExporter()
    )
    trace_hex = "1234567890abcdef1234567890abcdef"
    try:
        with observation.request_span(
            {"traceparent": f"00-{trace_hex}-1234567890abcdef-01"},
            request_id="req-safe",
            method="POST",
        ):
            with observation.observe_turn(
                conversation_id="conv-safe", runtime_version="v1", request_id="req-safe"
            ):
                runtime = observation.observation(runtime_version="v1", request_id="req-safe")
        observation.providers.force_flush()
        assert runtime.trace_id == trace_hex
        assert runtime.runtime_version == "v1"
        assert runtime.request_id == "req-safe"
        turn_span = next(span for span in spans.get_finished_spans() if span.name == "agent.turn")
        assert turn_span.attributes["agent.runtime.version"] == "v1"
    finally:
        observation.shutdown()


def test_model_success_timeout_and_schema_failure_have_bounded_outcomes_and_duration():
    class Gateway:
        def analyze(self, value):
            if value == "timeout":
                raise httpx.ReadTimeout("private prompt")
            if value == "schema":
                raise ValueError("private response")
            return SimpleNamespace(degraded=False)

    observation = AgentObservability.in_memory()
    gateway = ObservedModelGateway(Gateway(), observation)
    assert gateway.analyze("ok").degraded is False
    for value, error in (("timeout", httpx.ReadTimeout), ("schema", ValueError)):
        try:
            gateway.analyze(value)
        except error:
            pass
    outcomes = {
        point.attributes.get("outcome")
        for point in observation.points
        if point.name == "agent_model_outcome_total"
    }
    durations = [
        point
        for point in observation.points
        if point.name == "agent_model_request_duration_seconds"
    ]
    assert outcomes == {"success", "timeout", "schema_failure"}
    assert len(durations) == 3


def test_memory_reindex_emits_outcome_duration_and_backlog():
    class Service:
        def reindex_memories(self, *, limit):
            assert limit == 7
            return SimpleNamespace(degraded=False, failed=0, remaining=11)

    observation = AgentObservability.in_memory()
    result = ObservedMemoryService(Service(), observation).reindex_memories(limit=7)

    assert result.remaining == 11
    names = {point.name for point in observation.points}
    assert {
        "agent_memory_reindex_total",
        "agent_memory_reindex_duration_seconds",
        "agent_memory_reindex_backlog",
    } <= names


def test_trace_attribute_allowlist_rejects_adversarial_content_markers():
    observation = AgentObservability.in_memory()
    markers = {
        "user.message": "PRIVATE_USER_MESSAGE",
        "memory.content": "PRIVATE_MEMORY",
        "system.prompt": "PRIVATE_SYSTEM_PROMPT",
        "approval.token": "PRIVATE_APPROVAL_TOKEN",
        "confirmation.token": "PRIVATE_CONFIRMATION_TOKEN",
        "idempotency.key": "PRIVATE_IDEMPOTENCY_KEY",
        "address": "PRIVATE_ADDRESS",
        "phone": "13800000000",
    }
    with observation.span("privacy", attributes={**markers, "operation": "test"}):
        pass

    rendered = repr(observation.spans[-1].attributes)
    assert observation.spans[-1].attributes == {"operation": "test"}
    assert all(marker not in rendered for marker in markers.values())


def test_checkpoint_and_accepted_head_share_durable_publish_outcome_timing():
    class Checkpointer:
        def publish_accepted(self, *args, **kwargs):
            return 2

    observation = AgentObservability.in_memory()
    plan = SimpleNamespace(runtime_version="v2", expected_version=1)
    result = GraphExecutionResult(state=object(), interrupt=None, done=True)

    assert publish_accepted(Checkpointer(), "conv", plan, result, observability=observation) == 2
    names = {point.name for point in observation.points}
    assert {
        "agent_checkpoint_persist_total",
        "agent_checkpoint_persist_duration_seconds",
        "agent_accepted_head_publish_total",
        "agent_accepted_head_publish_duration_seconds",
    } <= names


def test_accepted_head_failure_has_no_completed_outcome():
    class FailingCheckpointer:
        def publish_accepted(self, *args, **kwargs):
            raise RuntimeError("private database payload")

    observation = AgentObservability.in_memory()
    plan = SimpleNamespace(runtime_version="v1", expected_version=0)
    result = GraphExecutionResult(state=object(), interrupt=None, done=True)

    try:
        publish_accepted(FailingCheckpointer(), "conv", plan, result, observability=observation)
    except RuntimeError:
        pass
    outcomes = [
        point.attributes.get("outcome")
        for point in observation.points
        if point.name == "agent_accepted_head_publish_total"
    ]
    assert outcomes == ["FAILED_INFRASTRUCTURE"]
