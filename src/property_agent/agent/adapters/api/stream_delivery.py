"""Bounded SSE delivery decoupled from the authoritative lifecycle execution."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator
from contextvars import copy_context
from threading import Condition, Thread
from time import perf_counter
from typing import Any

from property_agent.agent.stream_events import AgentStreamEvent, coerce_stream_event

DEFAULT_MAX_BUFFERED_EVENTS = 32


class BoundedEventBuffer:
    def __init__(self, limit: int = DEFAULT_MAX_BUFFERED_EVENTS) -> None:
        self.limit = max(4, min(limit, 128))
        self._items: deque[AgentStreamEvent] = deque()
        self._condition = Condition()
        self._finished = False
        self._delivery_closed = False
        self.dropped_progress = 0

    def put(self, event: AgentStreamEvent) -> None:
        with self._condition:
            if self._delivery_closed:
                return
            if len(self._items) >= self.limit and event.provisional:
                self.dropped_progress += 1
                return
            if len(self._items) >= self.limit:
                self._drop_one_progress()
            self._items.append(event)
            self._condition.notify()

    def _drop_one_progress(self) -> None:
        for index, item in enumerate(self._items):
            if item.provisional:
                del self._items[index]
                self.dropped_progress += 1
                return
        raise RuntimeError("stream buffer exhausted its authoritative-event reserve")

    def finish(self) -> None:
        with self._condition:
            self._finished = True
            self._condition.notify_all()

    def next(self) -> AgentStreamEvent | None:
        with self._condition:
            while not self._items and not self._finished:
                self._condition.wait()
            if self._items:
                return self._items.popleft()
            return None

    def disconnect(self) -> None:
        with self._condition:
            self._delivery_closed = True
            self._items.clear()
            self._condition.notify_all()


class BoundedStreamBridge:
    """Run the existing lifecycle once while bounding only presentation delivery."""

    def __init__(
        self,
        source: Callable[[], Any],
        *,
        observability: Any | None = None,
        max_buffered_events: int = DEFAULT_MAX_BUFFERED_EVENTS,
    ) -> None:
        self._source = source
        self._telemetry = observability
        self.buffer = BoundedEventBuffer(max_buffered_events)
        self._request_context = copy_context()

    def events(self) -> Iterator[AgentStreamEvent]:
        started = perf_counter()
        completed = False
        terminal_outcome = "unknown"
        first = True
        producer = Thread(
            target=lambda: self._request_context.run(self._produce),
            daemon=True,
            name="agent-sse-producer",
        )
        producer.start()
        try:
            while (event := self.buffer.next()) is not None:
                if first:
                    self._duration("agent_stream_first_event_duration_seconds", started)
                    first = False
                yield event
                completed = event.kind.value in {"FINAL", "FAILED"}
                if completed:
                    terminal_outcome = event.kind.value.lower()
        finally:
            if not completed:
                self.buffer.disconnect()
                self._count("agent_stream_total", outcome="client_disconnect")
            else:
                self._count("agent_stream_total", outcome=terminal_outcome)
            if self.buffer.dropped_progress:
                self._count("agent_stream_total", outcome="progress_coalesced")
            self._duration("agent_stream_duration_seconds", started)

    def _produce(self) -> None:
        runtime = "unknown"
        terminal = False
        source = iter(self._source())
        try:
            for event in source:
                event = coerce_stream_event(event)
                runtime = event.runtime_version
                self.buffer.put(event)
                if event.kind.value in {"FINAL", "FAILED"}:
                    terminal = True
                    break
        except Exception:
            if not terminal:
                self.buffer.put(AgentStreamEvent.failed(runtime, "execution_failure"))
                terminal = True
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    if not terminal:
                        self.buffer.put(AgentStreamEvent.failed(runtime, "cleanup_failure"))
                        terminal = True
            if not terminal:
                self.buffer.put(AgentStreamEvent.failed(runtime, "missing_terminal_event"))
            self.buffer.finish()

    def _count(self, name: str, **attributes: str) -> None:
        if self._telemetry is not None:
            self._telemetry.count(name, attributes=attributes)

    def _duration(self, name: str, started: float) -> None:
        if self._telemetry is not None:
            self._telemetry.duration(name, perf_counter() - started, attributes={})


__all__ = ["BoundedEventBuffer", "BoundedStreamBridge", "DEFAULT_MAX_BUFFERED_EVENTS"]
