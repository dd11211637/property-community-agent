"""Test-only adapter for legacy-style fake graphs."""

from typing import Any

from property_agent.agent.application.graph_engine import GraphExecutionResult


class TestGraphEngine:
    __test__ = False

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    @staticmethod
    def _result(payload: Any) -> GraphExecutionResult:
        if isinstance(payload, GraphExecutionResult):
            return payload
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
