"""Official LangGraph v2 runtime foundation for the Repair pilot."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import uuid4

from property_agent.agent.application.graph_engine import GraphExecutionResult
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.specialists.repair import RepairPilotSpecialist
from property_agent.agent.state import GraphState, ProposedAction
from property_agent.agent.subgraphs.repair import select_repair_tool
from property_agent.platform.application.hashing import canonical_hash, canonical_payload


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


def _classify(envelope: LangGraphState) -> LangGraphState:
    state = _state(envelope)
    text = str(state.slots.get("user_text") or "")
    if state.intent not in {None, "REPAIR"}:
        state.error = "UNSUPPORTED_PILOT_DOMAIN"
        state.add_message("assistant", "当前试点运行时仅支持报修业务。")
        return _update(state)
    if not state.intent and not any(cue in text for cue in ("报修", "故障", "工单", "WX-")):
        state.error = "UNSUPPORTED_PILOT_DOMAIN"
        state.add_message("assistant", "当前试点运行时仅支持报修业务。")
        return _update(state)
    state.intent = "REPAIR"
    return _update(state)


def _repair_select(envelope: LangGraphState) -> LangGraphState:
    state = _state(envelope)
    state.slots["tool"] = select_repair_tool(state)
    return _update(state)


def _repair_collect(envelope: LangGraphState) -> LangGraphState:
    from property_agent.agent.policies import missing_slots_for_tool

    state = _state(envelope)
    state.missing_slots = missing_slots_for_tool(str(state.slots["tool"]), state.slots)
    state.requested_slot = state.missing_slots[0] if state.missing_slots else None
    if state.missing_slots:
        state.add_message("assistant", "请补充报修的位置和问题描述。")
    return _update(state)


def _prepare_action(envelope: LangGraphState) -> LangGraphState:
    state = _state(envelope)
    params = {
        "description": str(state.slots.get("description") or ""),
        "location": str(state.slots.get("location") or ""),
        "urgency": str(state.slots.get("urgency") or "NORMAL"),
    }
    params_hash = canonical_hash(params)
    proposed = ProposedAction(
        capability="repair_create",
        params=params,
        params_hash=params_hash,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )
    state.proposed_action = proposed
    state.pending_action = {
        "action": proposed.capability,
        "params": proposed.params,
        "params_hash": proposed.params_hash,
        "issued_at": proposed.issued_at,
    }
    state.operation_level = "WRITE_LOW"
    return _update(state)


def _await_confirmation(envelope: LangGraphState) -> LangGraphState:
    from langgraph.types import interrupt

    state = _state(envelope)
    decision = interrupt(dict(state.pending_action or {}))
    state._resume = {"confirmed": bool(decision.get("confirmed"))}
    return _update(state)


def _cancel(envelope: LangGraphState) -> LangGraphState:
    state = _state(envelope)
    state.pending_action = None
    state.proposed_action = None
    state.add_message("assistant", "已取消本次报修。")
    return _update(state)


def _explain(envelope: LangGraphState) -> LangGraphState:
    return envelope


def _unsupported_route(envelope: LangGraphState) -> str:
    return "unsupported" if _state(envelope).error else "repair_select"


def _repair_route(envelope: LangGraphState) -> str:
    state = _state(envelope)
    if state.missing_slots:
        return "explain"
    return "prepare_action" if state.slots.get("tool") == "repair_create" else "repair_execute"


def _confirm_route(envelope: LangGraphState) -> str:
    decision = _state(envelope)._resume or {}
    return "repair_execute" if bool(decision.get("confirmed")) else "cancel"


def build_repair_pilot_graph(specialist: RepairPilotSpecialist):
    from langgraph.graph import END, START, StateGraph
    from langgraph.runtime import Runtime

    def execute(envelope: LangGraphState, runtime: Runtime[RuntimeContext]) -> LangGraphState:
        state = _state(envelope)
        specialist.invoke(state, runtime.context)
        state.pending_action = None
        state.proposed_action = None
        return _update(state)

    graph = StateGraph(state_schema=LangGraphState, context_schema=RuntimeContext)
    graph.add_node("classify_intent", _classify)
    graph.add_node("repair_select", _repair_select)
    graph.add_node("repair_collect", _repair_collect)
    graph.add_node("prepare_action", _prepare_action)
    graph.add_node("await_confirmation", _await_confirmation)
    graph.add_node("repair_execute", execute)
    graph.add_node("cancel", _cancel)
    graph.add_node("explain", _explain)
    graph.add_node("unsupported", _explain)
    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges("classify_intent", _unsupported_route)
    graph.add_edge("repair_select", "repair_collect")
    graph.add_conditional_edges("repair_collect", _repair_route)
    graph.add_edge("prepare_action", "await_confirmation")
    graph.add_conditional_edges("await_confirmation", _confirm_route)
    for node in ("repair_execute", "cancel", "explain", "unsupported"):
        graph.add_edge(node, END)
    return graph


class LangGraphEngine:
    """Official StateGraph engine using exact current-execution checkpoint events."""

    def __init__(self, saver: Any, specialist: RepairPilotSpecialist) -> None:
        self._graph = build_repair_pilot_graph(specialist).compile(checkpointer=saver)

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
                interrupt=dict(state.pending_action or {}) if interrupted else None,
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
