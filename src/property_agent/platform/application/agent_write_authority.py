"""Shared Application-Service helper for Agent-only authoritative approval."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.context import ExecutionSource


def business_command(command: Any) -> dict[str, Any]:
    values = asdict(command)
    values.pop("confirmation_token", None)
    values.pop("approval_ref", None)
    return values


def consume_agent_write(
    uow: Any,
    context: Any,
    command: Any,
    operation: str,
    parameters: dict[str, Any],
    confirmation_required: Any,
) -> None:
    if getattr(context, "execution_source", ExecutionSource.HUMAN) != ExecutionSource.AGENT:
        return
    token = getattr(command, "confirmation_token", None)
    if not token or not token.strip():
        raise confirmation_required()
    uow.confirmations.consume(
        approval_ref=getattr(command, "approval_ref", None),
        token=token.strip(),
        actor_id=context.actor_id,
        action=operation,
        parameter_hash=canonical_hash(parameters),
        request_id=context.request_id,
    )
