"""Accepted-head publication and trusted runtime construction helpers."""

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


def publish_accepted(checkpointer: Any, conversation_id: str, plan: Any, result: Any) -> None:
    """Publish only after graph durability and the lifecycle heartbeat assertion."""
    if checkpointer is None:
        return
    normalized = result_from_payload(result)
    checkpointer.publish_accepted(
        conversation_id,
        normalized.state,
        expected_version=plan.expected_version,
        runtime_cursor=normalized.runtime_cursor,
    )


def runtime_for(plan: Any, *, token: str | None = None) -> RuntimeContext:
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
        prepared_write=prepared,
    )


def cursor_for(checkpointer: Any, conversation_id: str) -> dict[str, Any] | None:
    if checkpointer is None:
        return None
    accepted = checkpointer.load_accepted(conversation_id)
    if accepted is None or accepted.runtime_cursor is None:
        return None
    return accepted.runtime_cursor.to_dict()
