"""Accepted-head publication and trusted runtime construction helpers."""

from time import perf_counter
from typing import Any

from property_agent.agent.application.graph_engine import GraphExecutionResult
from property_agent.agent.runtime import PreparedWrite, RuntimeContext
from property_agent.platform.application.hashing import canonical_hash


def result_from_payload(payload: Any) -> GraphExecutionResult:
    if isinstance(payload, GraphExecutionResult):
        return payload
    return GraphExecutionResult(
        state=payload["state"],
        interrupt=payload.get("interrupt"),
        done=bool(payload.get("done", True)),
        runtime_cursor=payload.get("runtime_cursor"),
    )


def publish_accepted(
    checkpointer: Any,
    conversation_id: str,
    plan: Any,
    result: Any,
    *,
    observability: Any | None = None,
) -> int | None:
    """Publish only after graph durability and the synchronous lease-fence assertion."""
    if checkpointer is None:
        return None
    normalized = result_from_payload(result)
    started = perf_counter()
    attributes = {"runtime": plan.runtime_version, "operation": "publish"}
    v1_overlap = plan.runtime_version == "v1"
    try:
        value = int(
            checkpointer.publish_accepted(
                conversation_id,
                normalized.state,
                expected_version=plan.expected_version,
                runtime_cursor=normalized.runtime_cursor,
            )
        )
    except Exception:
        if observability is not None:
            observability.count(
                "agent_accepted_head_publish_total",
                attributes={**attributes, "outcome": "FAILED_INFRASTRUCTURE"},
            )
            if v1_overlap:
                observability.count(
                    "agent_checkpoint_persist_total",
                    attributes={
                        **attributes,
                        "operation": "v1_accepted_snapshot",
                        "outcome": "FAILED_INFRASTRUCTURE",
                    },
                )
            observability.count(
                "agent_accepted_head_orphan_total",
                attributes={**attributes, "reason": "publish_failure"},
            )
        raise
    finally:
        if observability is not None:
            observability.duration(
                "agent_accepted_head_publish_duration_seconds",
                perf_counter() - started,
                attributes=attributes,
            )
            if v1_overlap:
                observability.duration(
                    "agent_checkpoint_persist_duration_seconds",
                    perf_counter() - started,
                    attributes={**attributes, "operation": "v1_accepted_snapshot"},
                )
    if observability is not None:
        observability.count(
            "agent_accepted_head_publish_total",
            attributes={**attributes, "outcome": "COMPLETED"},
        )
        if v1_overlap:
            observability.count(
                "agent_checkpoint_persist_total",
                attributes={
                    **attributes,
                    "operation": "v1_accepted_snapshot",
                    "outcome": "COMPLETED",
                },
            )
    return value


def runtime_for(
    plan: Any, *, token: str | None = None, observation: Any | None = None
) -> RuntimeContext:
    prepared = None
    if token is not None:
        params = dict(plan.state.pending_action or {})
        prepared = PreparedWrite(
            confirmation_token=token,
            idempotency_key=canonical_hash(
                {"conversation_id": plan.state.conversation_id, "action": params}
            ),
            approval_ref=plan.state.approval_ref,
            capability=params.get("tool"),
            params_hash=params.get("params_hash"),
            plan_id=params.get("plan_id"),
            plan_step_id=params.get("plan_step_id"),
        )
    return RuntimeContext.from_request_context(
        plan.ctx,
        conversation_id=plan.state.conversation_id,
        current_house_id=plan.state.current_house_id,
        observation=observation,
        prepared_write=prepared,
    )


def cursor_for(
    checkpointer: Any,
    conversation_id: str,
    *,
    observability: Any | None = None,
    runtime_version: str = "v1",
) -> dict[str, Any] | None:
    if checkpointer is None:
        return None
    accepted = checkpointer.load_accepted(conversation_id)
    outcome = "FOUND" if accepted is not None and accepted.runtime_cursor is not None else "ABSENT"
    if observability is not None:
        observability.count(
            "agent_exact_cursor_resolution_total",
            attributes={"runtime": runtime_version, "outcome": outcome},
        )
    if outcome == "ABSENT":
        return None
    return accepted.runtime_cursor.to_dict()
