"""Narrow graph-engine boundary used by the shared turn lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from property_agent.agent.graph_core import CompiledGraph
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.state import GraphState


@dataclass(frozen=True, slots=True)
class GraphExecutionResult:
    state: GraphState
    interrupt: Any | None
    done: bool
    runtime_cursor: dict[str, str | None] | None = None


@runtime_checkable
class GraphEngine(Protocol):
    """Execute a graph; lifecycle, accepted-head CAS, and authority stay outside."""

    def invoke(
        self, state: GraphState, *, thread_id: str, runtime: RuntimeContext
    ) -> GraphExecutionResult: ...

    def resume(
        self,
        thread_id: str,
        resume_value: Any,
        *,
        state: GraphState,
        runtime: RuntimeContext,
        runtime_cursor: dict[str, Any] | None,
    ) -> GraphExecutionResult: ...

    def invoke_stream(
        self, state: GraphState, *, thread_id: str, runtime: RuntimeContext
    ) -> Iterator[tuple[str, Any]]: ...

    def resume_stream(
        self,
        thread_id: str,
        resume_value: Any,
        *,
        state: GraphState,
        runtime: RuntimeContext,
        runtime_cursor: dict[str, Any] | None,
    ) -> Iterator[tuple[str, Any]]: ...


class LegacyGraphEngine:
    """Compatibility engine over the custom graph, compiled without persistence."""

    def __init__(self, graph: CompiledGraph) -> None:
        self._graph = graph

    @staticmethod
    def _result(payload: dict[str, Any]) -> GraphExecutionResult:
        return GraphExecutionResult(
            state=payload["state"],
            interrupt=payload.get("interrupt"),
            done=bool(payload.get("done", True)),
        )

    def invoke(self, state, *, thread_id, runtime):
        del runtime
        return self._result(self._graph.invoke(state, thread_id=thread_id))

    def resume(self, thread_id, resume_value, *, state, runtime, runtime_cursor):
        del runtime, runtime_cursor
        return self._result(self._graph.resume(thread_id, resume_value, state=state))

    def invoke_stream(self, state, *, thread_id, runtime):
        del runtime
        return self._graph.invoke_stream(state, thread_id=thread_id)

    def resume_stream(self, thread_id, resume_value, *, state, runtime, runtime_cursor):
        del runtime, runtime_cursor
        return self._graph.resume_stream(thread_id, resume_value, state=state)
