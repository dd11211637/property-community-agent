"""Official LangGraph v2 Supervisor runtime with exact accepted cursor output."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from property_agent.agent.application.graph_engine import GraphExecutionResult
from property_agent.agent.application.pending_confirmation import confirmation_envelope
from property_agent.agent.orchestration import PlanStatus, PlanStepStatus, SpecialistName
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.specialists.supervisor import Supervisor
from property_agent.agent.state import GraphState
from property_agent.platform.application.hashing import canonical_payload


class ObservedCheckpointSaver(BaseCheckpointSaver):
    """Non-authoritative proxy observing only the official saver `put` boundary."""

    def __init__(self, delegate: Any, observability: Any) -> None:
        super().__init__(serde=delegate.serde)
        self._delegate = delegate
        self._telemetry = observability

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def config_specs(self):
        return self._delegate.config_specs

    def get_tuple(self, config):
        return self._delegate.get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        return self._delegate.list(config, filter=filter, before=before, limit=limit)

    def put(self, config, checkpoint, metadata, new_versions):
        attributes = {"runtime": "v2", "operation": "langgraph_saver_put"}
        started = perf_counter()
        try:
            result = self._delegate.put(config, checkpoint, metadata, new_versions)
        except Exception:
            self._telemetry.count(
                "agent_checkpoint_persist_total",
                attributes={**attributes, "outcome": "FAILED_INFRASTRUCTURE"},
            )
            raise
        finally:
            self._telemetry.duration(
                "agent_checkpoint_persist_duration_seconds",
                perf_counter() - started,
                attributes=attributes,
            )
        self._telemetry.count(
            "agent_checkpoint_persist_total",
            attributes={**attributes, "outcome": "COMPLETED"},
        )
        return result

    def put_writes(self, config, writes, task_id, task_path=""):
        return self._delegate.put_writes(config, writes, task_id, task_path)

    def get_next_version(self, current, channel):
        return self._delegate.get_next_version(current, channel)


class LangGraphState(TypedDict):
    agent_state: dict[str, Any]


class LangGraphStateCodec:
    """Credential-free, primitive-only projection for internal v3 checkpoints."""

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
        raise TypeError(f"v3 checkpoint state contains a non-primitive value at {path}")

    @staticmethod
    def decode(payload: dict[str, Any]) -> GraphState:
        raw = payload.get("agent_state", payload)
        return GraphState.from_dict(dict(raw))


def _state(envelope: LangGraphState) -> GraphState:
    return LangGraphStateCodec.decode(envelope)


def _update(state: GraphState) -> LangGraphState:
    return LangGraphStateCodec.encode(state)


def _accept_confirmation_state(state: GraphState, supervisor: Supervisor) -> None:
    if state.plan is None and state.active_goal is not None:
        from property_agent.agent.react_contracts import GoalStatus

        pending = state.pending_action or {}
        if (
            state.active_goal.status is not GoalStatus.WAITING_CONFIRMATION
            or pending.get("goal_id") != state.active_goal.goal_id
        ):
            raise RuntimeError("confirmed action has no matching pending Goal")
        state.active_goal.status = GoalStatus.IN_PROGRESS
        return
    step = supervisor.current_step(state)
    if step is None or step.status != PlanStepStatus.PENDING_CONFIRMATION:
        raise RuntimeError("confirmed action has no matching pending plan step")
    state.plan = state.plan.replace_step(replace(step, status=PlanStepStatus.PENDING))
    state.plan = replace(state.plan, status=PlanStatus.ACTIVE)
    if state.active_goal is not None:
        from property_agent.agent.react_contracts import GoalStatus

        state.active_goal.status = GoalStatus.IN_PROGRESS


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

    def react_reason(envelope: LangGraphState, runtime: Runtime[RuntimeContext]) -> LangGraphState:
        state = _state(envelope)
        supervisor.react.reason(state, runtime.context)
        return _update(state)

    def react_action(envelope: LangGraphState, runtime: Runtime[RuntimeContext]) -> LangGraphState:
        state = _state(envelope)
        supervisor.react.action(state, runtime.context)
        return _update(state)

    def await_confirmation(envelope: LangGraphState) -> LangGraphState:
        state = _state(envelope)
        decision = interrupt(confirmation_envelope(state))
        state._resume = {"confirmed": bool(decision.get("confirmed"))}
        return _update(state)

    def accept_confirmation(envelope: LangGraphState) -> LangGraphState:
        state = _state(envelope)
        _accept_confirmation_state(state, supervisor)
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
    graph.add_node("react_reason", react_reason)
    graph.add_node("react_action", react_action)
    graph.add_node("accept_confirmation", accept_confirmation)
    graph.add_node("cancel", cancel)
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", _supervisor_route)
    graph.add_conditional_edges("react_reason", _react_reason_route)
    graph.add_edge("react_action", "supervisor")
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
        goal = state.active_goal
        if goal is None:
            return "synthesize"
        from property_agent.agent.react_contracts import GoalStatus

        if goal.status is GoalStatus.WAITING_CONFIRMATION:
            return "await_confirmation"
        if goal.status in {
            GoalStatus.COMPLETED,
            GoalStatus.PARTIAL,
            GoalStatus.NEEDS_CLARIFICATION,
            GoalStatus.HANDOVER,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }:
            return "synthesize"
        decision = goal.last_decision
        return (
            "react_action"
            if decision is not None and decision.decision.value == "ACT"
            else "react_reason"
        )
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
    if step and step.capability is None:
        decision = getattr(state.active_goal, "last_decision", None)
        if decision is not None and decision.decision.value == "ACT":
            return "react_action"
        return "react_reason"
    routes = {
        SpecialistName.REPAIR: "repair_specialist",
        SpecialistName.BILLING: "billing_specialist",
        SpecialistName.ANNOUNCEMENT: "announcement_specialist",
        SpecialistName.INSPECTION: "inspection_specialist",
    }
    return routes.get(step.specialist, "synthesize") if step else "synthesize"


def _react_reason_route(envelope: LangGraphState) -> str:
    state = _state(envelope)
    if state.plan is None:
        decision = getattr(state.active_goal, "last_decision", None)
        return (
            "react_action"
            if decision is not None and decision.decision.value == "ACT"
            else "supervisor"
        )
    if state.plan.status != PlanStatus.ACTIVE:
        return "supervisor"
    decision = getattr(state.active_goal, "last_decision", None)
    return (
        "react_action"
        if decision is not None and decision.decision.value == "ACT"
        else "supervisor"
    )


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


def build_saver_resource(
    *, dsn: str | None = None, in_memory: bool = False, observability: Any | None = None
):
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)
    if in_memory:
        from langgraph.checkpoint.memory import MemorySaver

        saver = MemorySaver(serde=serde)
        return LangGraphSaverResource(
            ObservedCheckpointSaver(saver, observability) if observability is not None else saver
        )
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
    saver = PostgresSaver(pool, serde=serde)
    if observability is not None:
        saver = ObservedCheckpointSaver(saver, observability)
    return LangGraphSaverResource(saver, pool)
