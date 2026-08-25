"""Application-owned bounded execution resources for canonical Agent streams."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import Lock
from time import perf_counter
from typing import Any


class StreamExecutionRejected(RuntimeError):
    """The application is draining or all bounded producer slots are occupied."""


class BoundedStreamExecutionRegistry:
    """Own producer futures without owning Agent lifecycle or business state."""

    def __init__(self, max_concurrency: int, observability: Any | None = None) -> None:
        if max_concurrency <= 0:
            raise ValueError("stream producer concurrency must be positive")
        self._limit = max_concurrency
        self._telemetry = observability
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="agent-sse-producer",
        )
        self._lock = Lock()
        self._futures: set[Future[Any]] = set()
        self._accepting = True

    def submit(self, producer: Callable[[], None]) -> Future[Any]:
        with self._lock:
            if not self._accepting:
                raise StreamExecutionRejected("stream execution registry is draining")
            if len(self._futures) >= self._limit:
                raise StreamExecutionRejected("stream execution capacity is exhausted")
            future = self._executor.submit(producer)
            self._futures.add(future)
            active = len(self._futures)
        future.add_done_callback(self._completed)
        self._count("agent_stream_execution_total", outcome="admitted")
        self._active(active)
        return future

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": "ACCEPTING" if self._accepting else "DRAINING",
                "active": len(self._futures),
                "capacity": self._limit,
            }

    def shutdown(self, grace_seconds: float) -> bool:
        if grace_seconds < 0:
            raise ValueError("stream shutdown grace must not be negative")
        started = perf_counter()
        with self._lock:
            self._accepting = False
            futures = set(self._futures)
        _, pending = wait(futures, timeout=grace_seconds)
        drained = not pending
        self._count(
            "agent_stream_execution_total",
            outcome="drained" if drained else "drain_timeout",
        )
        self._duration("agent_stream_execution_drain_duration_seconds", started)
        self._executor.shutdown(wait=False, cancel_futures=False)
        return drained

    def _completed(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.discard(future)
            active = len(self._futures)
        outcome = "failed" if future.exception() is not None else "completed"
        self._count("agent_stream_execution_total", outcome=outcome)
        self._active(active)

    def _active(self, value: int) -> None:
        if self._telemetry is not None:
            self._telemetry.value("agent_stream_active_producers", value, attributes={})

    def _count(self, name: str, **attributes: str) -> None:
        if self._telemetry is not None:
            self._telemetry.count(name, attributes=attributes)

    def _duration(self, name: str, started: float) -> None:
        if self._telemetry is not None:
            self._telemetry.duration(name, perf_counter() - started, attributes={})


def install_stream_execution(app: Any, settings: Any, services: dict[str, Any]) -> None:
    registry = BoundedStreamExecutionRegistry(
        settings.agent_stream_max_concurrency,
        app.state.agent_observability,
    )
    app.state.agent_stream_executions = registry
    services["agent_stream_executions"] = registry


async def drain_stream_executions(app: Any, grace_seconds: float) -> bool:
    """Stop admission and boundedly drain producers before runtime resources close."""
    registry = getattr(app.state, "agent_stream_executions", None)
    if registry is None:
        return True
    return await asyncio.to_thread(registry.shutdown, grace_seconds)


__all__ = [
    "BoundedStreamExecutionRegistry",
    "StreamExecutionRejected",
    "drain_stream_executions",
    "install_stream_execution",
]
