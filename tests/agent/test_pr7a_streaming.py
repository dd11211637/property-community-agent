from __future__ import annotations

from contextvars import ContextVar
from threading import Event

from property_agent.agent.adapters.api.presentation import stream_turn_data, wire_events
from property_agent.agent.adapters.api.stream_delivery import (
    BoundedEventBuffer,
    BoundedStreamBridge,
)
from property_agent.agent.observability import AgentObservability
from property_agent.agent.stream_events import AgentStreamEvent, StreamEventKind


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

    events = list(BoundedStreamBridge(source).events())

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
    events = list(
        BoundedStreamBridge(lambda: iter((AgentStreamEvent.started("conv", "v1"),))).events()
    )

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

    events = list(BoundedStreamBridge(source).events())

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

    stream = BoundedStreamBridge(source, observability=telemetry).events()
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

    assert list(BoundedStreamBridge(source).events())[0].kind is StreamEventKind.FINAL
