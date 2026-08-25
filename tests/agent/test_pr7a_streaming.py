from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import property_agent.platform.container as container_module
from property_agent.agent.adapters.api.presentation import stream_turn_data, wire_events
from property_agent.agent.adapters.api.stream_delivery import (
    BoundedEventBuffer,
    BoundedStreamBridge,
)
from property_agent.agent.application.stream_execution import (
    BoundedStreamExecutionRegistry,
    StreamExecutionRejected,
    drain_stream_executions,
)
from property_agent.agent.observability import AgentObservability
from property_agent.agent.stream_events import AgentStreamEvent, StreamEventKind


@contextmanager
def _owned_bridge(source, *, observability=None, max_concurrency=2):
    registry = BoundedStreamExecutionRegistry(max_concurrency, observability)
    try:
        yield BoundedStreamBridge(
            source,
            registry=registry,
            observability=observability,
        )
    finally:
        registry.shutdown(2)


def test_bounded_buffer_drops_only_progress_and_reserves_final() -> None:
    buffer = BoundedEventBuffer(limit=4)
    buffer.put(AgentStreamEvent.started("conv", "v1"))
    for index in range(20):
        buffer.put(AgentStreamEvent.progress(f"internal_node_{index}", "v1", active=True))
    final = AgentStreamEvent.final(object(), "v1")
    buffer.put(final)
    buffer.finish()

    delivered = []
    while (event := buffer.next()) is not None:
        delivered.append(event)

    assert delivered[-1] is final
    assert buffer.dropped_progress > 0
    assert all(event.kind is not StreamEventKind.FAILED for event in delivered)


def test_bridge_converts_execution_failure_to_one_safe_terminal() -> None:
    def source():
        yield AgentStreamEvent.started("conv", "v2")
        raise RuntimeError("secret payload must not cross the wire")

    with _owned_bridge(source) as bridge:
        events = list(bridge.events())

    assert [event.kind for event in events] == [
        StreamEventKind.TURN_STARTED,
        StreamEventKind.FAILED,
    ]
    assert events[-1].runtime_version == "v2"
    assert events[-1].data == {
        "category": "execution_failure",
        "recoverable_via_status": True,
    }


def test_bridge_emits_failure_when_source_omits_authoritative_terminal() -> None:
    with _owned_bridge(lambda: iter((AgentStreamEvent.started("conv", "v1"),))) as bridge:
        events = list(bridge.events())

    assert events[-1].kind is StreamEventKind.FAILED
    assert events[-1].data["category"] == "missing_terminal_event"


def test_bridge_stops_after_first_authoritative_terminal() -> None:
    cleaned_up = Event()

    def source():
        try:
            yield AgentStreamEvent.final(object(), "v1")
            raise RuntimeError("must never replace an accepted final")
        finally:
            cleaned_up.set()

    with _owned_bridge(source) as bridge:
        events = list(bridge.events())

    assert [event.kind for event in events] == [StreamEventKind.FINAL]
    assert cleaned_up.is_set()


def test_disconnect_does_not_cancel_canonical_execution() -> None:
    continue_execution = Event()
    side_effect_completed = Event()
    telemetry = AgentObservability.in_memory()

    def source():
        yield AgentStreamEvent.started("conv", "v1")
        continue_execution.wait(timeout=2)
        side_effect_completed.set()
        yield AgentStreamEvent.final(object(), "v1")

    with _owned_bridge(source, observability=telemetry) as bridge:
        stream = bridge.events()
        assert next(stream).kind is StreamEventKind.TURN_STARTED
        stream.close()
        continue_execution.set()
        assert side_effect_completed.wait(timeout=2)
    assert any(
        point.name == "agent_stream_total"
        and point.attributes.get("outcome") == "client_disconnect"
        for point in telemetry.points
    )


def test_progress_wire_contract_hides_internal_node_name() -> None:
    event = AgentStreamEvent.progress("private_graph_node", "v2", active=True)

    assert wire_events(event) == [("tool_started", {"stage": "executing_capability"})]


def test_stream_turn_snapshot_is_an_explicit_privacy_allowlist() -> None:
    source = {
        "conversation_id": "conv",
        "status": "ACTIVE",
        "done": False,
        "intent": "REPAIR",
        "confidence": 1.0,
        "operation_level": "READ_ONLY",
        "reply": "safe",
        "facts": None,
        "missing_slots": [],
        "requested_slot": None,
        "slot_prompt": None,
        "handover_required": False,
        "pending_confirmation": None,
        "messages": [{"content": "secret history"}],
        "agent_trace": [{"private": "trace"}],
        "error": "internal stack",
        "graph_state": {"private": True},
    }

    snapshot = stream_turn_data(source)

    assert snapshot["reply"] == "safe"
    assert set(source) - set(snapshot) == {
        "messages",
        "agent_trace",
        "error",
        "graph_state",
    }


def test_delivery_thread_preserves_trusted_request_context() -> None:
    trusted_context = ContextVar("trusted_test_context", default=None)
    trusted_context.set("server-owned")

    def source():
        assert trusted_context.get() == "server-owned"
        yield AgentStreamEvent.final(object(), "v1")

    with _owned_bridge(source) as bridge:
        assert list(bridge.events())[0].kind is StreamEventKind.FINAL


def test_application_registry_has_no_unbounded_waiting_queue() -> None:
    release = Event()
    registry = BoundedStreamExecutionRegistry(1)
    try:
        registry.submit(lambda: release.wait(timeout=2))
        assert registry.snapshot() == {"state": "ACCEPTING", "active": 1, "capacity": 1}
        with pytest.raises(StreamExecutionRejected, match="capacity"):
            registry.submit(lambda: None)
    finally:
        release.set()
        assert registry.shutdown(2) is True


@pytest.mark.asyncio
async def test_application_shutdown_drains_disconnected_producer_before_resources(
    monkeypatch,
) -> None:
    continue_execution = Event()
    producer_finished = Event()
    order = []
    registry = BoundedStreamExecutionRegistry(1)

    def source():
        yield AgentStreamEvent.started("conv", "v1")
        continue_execution.wait(timeout=2)
        order.append("producer_finished")
        producer_finished.set()
        yield AgentStreamEvent.final(object(), "v1")

    bridge = BoundedStreamBridge(source, registry=registry)
    delivery = bridge.events()
    assert next(delivery).kind is StreamEventKind.TURN_STARTED
    delivery.close()
    app = SimpleNamespace(state=SimpleNamespace(agent_stream_executions=registry))
    monkeypatch.setattr(
        "property_agent.platform.container.settings.agent_stream_shutdown_grace_seconds", 2.0
    )

    drain = asyncio.create_task(drain_stream_executions(app, 2.0))
    await asyncio.sleep(0)
    assert registry.snapshot()["state"] == "DRAINING"
    continue_execution.set()
    assert await drain is True
    order.append("runtime_resources_closed")

    assert producer_finished.is_set()
    assert order == ["producer_finished", "runtime_resources_closed"]


@pytest.mark.asyncio
async def test_fastapi_lifespan_drains_detached_stream_before_runtime_shutdown(
    monkeypatch,
) -> None:
    order = []
    continue_execution = Event()
    registry = BoundedStreamExecutionRegistry(1)

    class Worker:
        def __init__(self) -> None:
            self.stopped = asyncio.Event()

        async def run(self):
            await self.stopped.wait()

        async def stop(self):
            self.stopped.set()

    dispatcher = Worker()
    scheduler = Worker()

    def build_container(app):
        app.state.outbox_dispatcher = dispatcher
        app.state.announcement_service = object()
        app.state.agent_stream_executions = registry

    monkeypatch.setattr(container_module, "get_async_engine", lambda: object())
    monkeypatch.setattr(container_module, "get_async_session_factory", lambda: object())
    monkeypatch.setattr(container_module, "build_production_container", build_container)
    monkeypatch.setattr(container_module, "AnnouncementScheduler", lambda _service: scheduler)
    monkeypatch.setattr(
        container_module,
        "close_runtime_resources",
        lambda _app: order.append("runtime_resources_closed"),
    )
    monkeypatch.setattr(container_module.settings, "agent_stream_shutdown_grace_seconds", 2.0)

    app = FastAPI()
    async with container_module.lifespan(app):

        def source():
            yield AgentStreamEvent.started("conv", "v1")
            continue_execution.wait(timeout=2)
            order.append("producer_finished")
            yield AgentStreamEvent.final(object(), "v1")

        delivery = BoundedStreamBridge(source, registry=registry).events()
        assert next(delivery).kind is StreamEventKind.TURN_STARTED
        delivery.close()

        async def release_producer():
            await asyncio.sleep(0.05)
            continue_execution.set()

        asyncio.create_task(release_producer())

    assert order == ["producer_finished", "runtime_resources_closed"]
