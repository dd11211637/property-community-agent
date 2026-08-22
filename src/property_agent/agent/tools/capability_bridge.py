"""Compatibility bridge from legacy graph tools to typed capabilities."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from property_agent.agent.capabilities.contracts import CapabilityRuntimeContext
from property_agent.agent.runtime import ExecutionPolicy, RuntimeContext


def invoke_capability(
    executor: Any,
    context_provider: Any,
    state: Any,
    name: str,
    payload: dict[str, Any],
    *,
    confirmed: bool = False,
    write: Any = None,
) -> dict[str, Any]:
    context = context_provider(state)
    trusted = RuntimeContext.from_request_context(
        context,
        conversation_id=state.conversation_id,
        current_house_id=state.current_house_id,
        execution_policy=ExecutionPolicy(allowlist=frozenset({name})),
    )
    invocation = replace(
        state.capability_invocation,
        selected_capability=name,
        human_confirmed=confirmed,
    )
    result = executor.execute(
        name,
        payload,
        CapabilityRuntimeContext(
            context,
            state.current_house_id,
            legacy_state=state,
            write=write,
            trusted_runtime=trusted,
        ),
        invocation,
    )
    if result.fingerprint is not None:
        state.capability_invocation = replace(
            invocation,
            step=invocation.step + 1,
            calls_made=invocation.calls_made + 1,
            prior_fingerprints=invocation.prior_fingerprints | {result.fingerprint},
            fingerprint=result.fingerprint,
        )
    if result.error is not None:
        if result.error.cause is not None:
            raise result.error.cause
        raise RuntimeError(f"{result.error.code}: {result.error.message}")
    return result.output.data
