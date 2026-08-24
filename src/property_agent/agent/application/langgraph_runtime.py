"""Official LangGraph v2 Supervisor runtime with exact accepted cursor output."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any, TypedDict
from uuid import uuid4

from property_agent.agent.application.graph_engine import GraphExecutionResult
from property_agent.agent.application.pending_confirmation import confirmation_envelope
from property_agent.agent.orchestration import PlanStatus, PlanStepStatus, SpecialistName
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.specialists.supervisor import Supervisor
from property_agent.agent.state import GraphState
from property_agent.platform.application.hashing import canonical_payload


class LangGraphState(TypedDict):
    agent_state: dict[str, Any]


class LangGraphStateCodec:
    """Credential-free, primitive-only projection for internal v2 checkpoints."""

    _TRUSTED_SLOTS = frozenset(
        {
            "actor_id",
            "community_id",
            "current_house_id",
            "house_id",
            "bound_house_ids",
            "roles",
            "request_id",
            "confirmation_token",
            "approval_ref",
            "idempotency_key",
            "execution_source",
            "lease",
            "fence",
        }
    )

    @classmethod
    def encode(cls, state: GraphState) -> LangGraphState:
        payload = canonical_payload(state.to_dict())
        for key in (
            "actor_id",
            "community_id",
            "current_house_id",
            "confirmation_token",
            "approval_ref",
            "trusted_context",
        ):
            payload.pop(key, None)
        payload["slots"] = {
            key: value
            for key, value in dict(payload.get("slots") or {}).items()
            if key not in cls._TRUSTED_SLOTS
        }
        orchestration = dict(payload.get("orchestration") or {})
        decision = orchestration.get("resume")
        orchestration["resume"] = (
            {"confirmed": bool(decision.get("confirmed"))} if isinstance(decision, dict) else None
        )
        payload["orchestration"] = orchestration
        cls._require_primitives(payload)
        return {"agent_state": payload}

    @classmethod
    def _require_primitives(cls, value: Any, path: str = "agent_state") -> None:
        if value is None or isinstance(value, str | int | float | bool):
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                cls._require_primitives(item, f"{path}[{index}]")
            return
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            for key, item in value.items():
                cls._require_primitives(item, f"{path}.{key}")
            return
        raise TypeError(f"v2 checkpoint state contains a non-primitive value at {path}")

    @staticmethod
    def decode(payload: dict[str, Any]) -> GraphState:
        raw = payload.get("agent_state", payload)
        return GraphState.from_dict(dict(raw))


def _state(envelope: LangGraphState) -> GraphState:
    return LangGraphStateCodec.decode(envelope)


def _update(state: GraphState) -> LangGraphState:
    return LangGraphStateCodec.encode(state)


def build_supervisor_graph(supervisor: Supervisor):
    """Build one sequential Supervisor graph with four explicit specialist nodes."""
    from langgraph.graph import END, START, StateGraph
    from langgraph.runtime import Runtime
    from langgraph.types import interrupt

    def supervise(envelope: LangGraphState, runtime: Runtime[RuntimeContext]) -> LangGraphState:
        state = _state(envelope)
        supervisor.prepare(state, runtime.context)
        return _update(state)

    def specialist(envelope: LangGraphState, runtime: Runtime[RuntimeContext]) -> LangGraphState:
        state = _state(envelope)
        supervisor.run_current(state, runtime.context)
        return _update(state)

    def await_confirmation(envelope: LangGraphState) -> LangGraphState:
        state = _state(envelope)
        decision = interrupt(confirmation_envelope(state))
        state._resume = {"confirmed": bool(decision.get("confirmed"))}
        return _update(state)

    def accept_confirmation(envelope: LangGraphState) -> LangGraphState:
        state = _state(envelope)
        step = supervisor.current_step(state)
        if step is None or step.status != PlanStepStatus.PENDING_CONFIRMATION:
            raise RuntimeError("confirmed action has no matching pending plan step")
        state.plan = state.plan.replace_step(replace(step, status=PlanStepStatus.PENDING))
        state.plan = replace(state.plan, status=PlanStatus.ACTIVE)
        return _update(state)

    def cancel(envelope: LangGraphState) -> LangGraphState:
        state = _state(envelope)
        supervisor.cancel_current(state)
        return _update(state)

    def synthesize(envelope: LangGraphState) -> LangGraphState:
        state = _state(envelope)
        message = supervisor.synthesize(state)
        if not state.messages or state.messages[-1].get("content") != message:
            state.add_message("assistant", message)
        return _update(state)

    graph = StateGraph(state_schema=LangGraphState, context_schema=RuntimeContext)
    graph.add_node("supervisor", supervise)
    for name in ("repair", "billing", "announcement", "inspection"):
        graph.add_node(f"{name}_specialist", specialist)
    graph.add_node("await_confirmation", await_confirmation)
    graph.add_node("accept_confirmation", accept_confirmation)
    graph.add_node("cancel", cancel)
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", _supervisor_route)
    for name in ("repair", "billing", "announcement", "inspection"):
        graph.add_edge(f"{name}_specialist", "supervisor")
    graph.add_conditional_edges("await_confirmation", _confirmation_route)
    graph.add_edge("accept_confirmation", "supervisor")
    graph.add_edge("cancel", "supervisor")
    graph.add_edge("synthesize", END)
    return graph


def _supervisor_route(envelope: LangGraphState) -> str:
    state = _state(envelope)
    if state.plan is None:
        return "synthesize"
    if state.plan.status == PlanStatus.WAITING_CONFIRMATION:
        return "await_confirmation"
    terminal = {
        PlanStatus.COMPLETED,
        PlanStatus.PARTIAL,
        PlanStatus.FAILED,
        PlanStatus.NEEDS_CLARIFICATION,
        PlanStatus.HANDOVER,
    }
    if state.plan.status in terminal:
        return "synthesize"
    step = next(
        (item for item in state.plan.steps if item.step_id == state.plan.current_step_id), None
    )
    routes = {
        SpecialistName.REPAIR: "repair_specialist",
        SpecialistName.BILLING: "billing_specialist",
        SpecialistName.ANNOUNCEMENT: "announcement_specialist",
        SpecialistName.INSPECTION: "inspection_specialist",
    }
    return routes.get(step.specialist, "synthesize") if step else "synthesize"


def _confirmation_route(envelope: LangGraphState) -> str:
    decision = _state(envelope)._resume or {}
    return "accept_confirmation" if bool(decision.get("confirmed")) else "cancel"


class LangGraphEngine:
    """Official StateGraph engine using exact current-execution checkpoint events."""

    def __init__(self, saver: Any, supervisor: Supervisor) -> None:
        self._graph = build_supervisor_graph(supervisor).compile(checkpointer=saver)

    def invoke(self, state, *, thread_id, runtime):
        return self._consume(self.invoke_stream(state, thread_id=thread_id, runtime=runtime))

    def resume(self, thread_id, resume_value, *, state, runtime, runtime_cursor):
        return self._consume(
            self.resume_stream(
                thread_id,
                resume_value,
                state=state,
                runtime=runtime,
                runtime_cursor=runtime_cursor,
            )
        )

    @staticmethod
    def _consume(events: Iterator[tuple[str, Any]]) -> GraphExecutionResult:
        final = None
        for kind, payload in events:
            if kind == "__final__":
                final = payload
        if final is None:
            raise RuntimeError("LangGraph execution produced no final checkpoint event")
        return final

    def invoke_stream(self, state, *, thread_id, runtime):
        internal_thread = f"lg:{thread_id}:{uuid4()}"
        config = {"configurable": {"thread_id": internal_thread, "checkpoint_ns": ""}}
        yield from self._stream(LangGraphStateCodec.encode(state), config, runtime)

    def resume_stream(self, thread_id, resume_value, *, state, runtime, runtime_cursor):
        del thread_id, state
        if not runtime_cursor or not runtime_cursor.get("checkpoint_id"):
            raise RuntimeError("accepted LangGraph checkpoint cursor is required for resume")
        from langgraph.types import Command

        config = {"configurable": dict(runtime_cursor)}
        yield from self._stream(Command(resume=resume_value), config, runtime)

    def _stream(self, graph_input: Any, config: dict[str, Any], runtime: RuntimeContext):
        last_values: dict[str, Any] | None = None
        last_cursor: dict[str, Any] | None = None
        interrupted = False
        for event in self._graph.stream(
            graph_input,
            config,
            context=runtime,
            stream_mode=["checkpoints", "tasks", "values"],
            version="v2",
            durability="sync",
        ):
            kind = event.get("type")
            data = event.get("data") or {}
            if kind == "tasks":
                node = data.get("name")
                yield ("node_exit" if "result" in data else "node_enter", {"node": node})
            elif kind == "values":
                last_values = data
            elif kind == "checkpoints" and not event.get("ns"):
                last_values = data.get("values") or last_values
                last_cursor = dict(data["config"]["configurable"])
                interrupted = bool(data.get("next"))
        if last_values is None or last_cursor is None:
            raise RuntimeError("LangGraph did not durably emit a root checkpoint")
        state = LangGraphStateCodec.decode(last_values)
        state.actor_id = runtime.actor_id
        state.community_id = runtime.community_id
        state.current_house_id = runtime.current_house_id
        state._interrupt_node = "await_confirmation" if interrupted else None
        yield (
            "__final__",
            GraphExecutionResult(
                state=state,
                interrupt=confirmation_envelope(state) if interrupted else None,
                done=not interrupted,
                runtime_cursor={
                    "thread_id": str(last_cursor["thread_id"]),
                    "checkpoint_ns": str(last_cursor.get("checkpoint_ns") or ""),
                    "checkpoint_id": str(last_cursor["checkpoint_id"]),
                },
            ),
        )


@dataclass(slots=True)
class LangGraphSaverResource:
    saver: Any
    pool: Any | None = None

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()


def build_saver_resource(*, dsn: str | None = None, in_memory: bool = False):
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)
    if in_memory:
        from langgraph.checkpoint.memory import MemorySaver

        return LangGraphSaverResource(MemorySaver(serde=serde))
    if not dsn:
        raise ValueError("PostgreSQL DSN is required for the v2 production saver")
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        dsn,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        min_size=1,
        open=True,
    )
    return LangGraphSaverResource(PostgresSaver(pool, serde=serde), pool)
