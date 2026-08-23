"""PR3 regression: duplicate-invocation fingerprint protection is preserved.

The migrated production wrappers (``repair._invoke_capability``,
``billing._invoke_capability`` and the common ``capability_bridge.invoke_capability``
used by announcement/inspection) must NOT clear ``prior_fingerprints`` before
calling ``CapabilityExecutor``. ``CapabilityInvocationState`` is the mutable
canonical owner of checkpointed fingerprints; if they are wiped on every
invocation, ``CapabilityPolicy.DUPLICATE_INVOCATION`` can never fire on the
migrated path.

These tests drive the wrappers directly with counting mock adapters so the
duplicate guard is observable without a database.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest

from property_agent.agent.capabilities.adapters.billing import BillingQueryOutput
from property_agent.agent.capabilities.adapters.inspection import InspectionDataOutput
from property_agent.agent.capabilities.adapters.repair import RepairListOutput
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import (
    CapabilityInvocationState,
    CapabilityRuntimeContext,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import CapabilityPolicy
from property_agent.agent.runtime import ExecutionPolicy, RuntimeContext
from property_agent.agent.state import GraphState
from property_agent.agent.tools.billing import _invoke_capability as billing_invoke
from property_agent.agent.tools.capability_bridge import (
    LegacyCapabilityError,
    invoke_capability,
)
from property_agent.agent.tools.repair import _invoke_capability as repair_invoke


def _repair_executor(adapter):
    return CapabilityExecutor(
        default_capability_registry(), CapabilityPolicy(), {"repair_list": adapter}
    )


def _billing_executor(adapter):
    return CapabilityExecutor(
        default_capability_registry(), CapabilityPolicy(), {"billing_query": adapter}
    )


def _ctx_provider():
    return lambda _s: object()


# ---------------------------------------------------------------------------
# 1. Same AgentState, same canonical capability, same typed input.
# ---------------------------------------------------------------------------


def test_repair_wrapper_blocks_duplicate_and_skips_adapter():
    adapter = Mock(return_value=RepairListOutput(count=0, items=()))
    executor = _repair_executor(adapter)
    state = GraphState(conversation_id="dup-repair-1")
    payload = {"statuses": (), "limit": 20}

    first = repair_invoke(executor, _ctx_provider(), state, "repair_list", payload)
    assert first.error is None
    assert adapter.call_count == 1

    second = repair_invoke(executor, _ctx_provider(), state, "repair_list", payload)
    assert second.error is not None
    assert second.error.code == "DUPLICATE_INVOCATION"
    # The adapter must not be re-executed for a duplicate invocation.
    assert adapter.call_count == 1


def test_billing_wrapper_blocks_duplicate_and_skips_adapter():
    adapter = Mock(return_value=BillingQueryOutput(query_type="list"))
    executor = _billing_executor(adapter)
    state = GraphState(conversation_id="dup-billing-1")
    payload = {"query_type": "list", "period": None, "fee_type": None, "bill_id": None}

    first = billing_invoke(executor, _ctx_provider(), state, "billing_query", payload)
    assert first.error is None
    assert adapter.call_count == 1

    second = billing_invoke(executor, _ctx_provider(), state, "billing_query", payload)
    assert second.error is not None
    assert second.error.code == "DUPLICATE_INVOCATION"
    assert adapter.call_count == 1


# ---------------------------------------------------------------------------
# 2. Same AgentState, same capability, different typed input -> allowed.
# ---------------------------------------------------------------------------


def test_repair_wrapper_allows_different_input():
    adapter = Mock(return_value=RepairListOutput(count=0, items=()))
    executor = _repair_executor(adapter)
    state = GraphState(conversation_id="dup-repair-2")

    first = repair_invoke(
        executor, _ctx_provider(), state, "repair_list", {"statuses": (), "limit": 20}
    )
    assert first.error is None

    second = repair_invoke(
        executor,
        _ctx_provider(),
        state,
        "repair_list",
        {"statuses": ("OPEN",), "limit": 20},
    )
    assert second.error is None
    assert adapter.call_count == 2


# ---------------------------------------------------------------------------
# Common bridge (announcement / inspection migrated path).
# ---------------------------------------------------------------------------


def test_bridge_blocks_duplicate_invocation():
    # inspection_list is a read capability whose output carries the ``.data``
    # projection the legacy bridge returns, so this exercises the real bridge
    # return path used by announcement/inspection.
    adapter = Mock(return_value=InspectionDataOutput(data={"items": []}))
    executor = CapabilityExecutor(
        default_capability_registry(), CapabilityPolicy(), {"inspection_list": adapter}
    )
    state = GraphState(conversation_id="dup-bridge-1")
    payload = {
        "target": "task",
        "statuses": (),
        "risk_levels": (),
        "assigned_to_me": False,
        "limit": 20,
    }

    invoke_capability(executor, _ctx_provider(), state, "inspection_list", payload)
    assert adapter.call_count == 1

    with pytest.raises(LegacyCapabilityError) as exc:
        invoke_capability(executor, _ctx_provider(), state, "inspection_list", payload)
    assert exc.value.code == "DUPLICATE_INVOCATION"
    assert adapter.call_count == 1


# ---------------------------------------------------------------------------
# 3. prior_fingerprints checkpoint round-trip stays a frozenset.
# ---------------------------------------------------------------------------


def test_prior_fingerprints_stays_frozenset_through_checkpoint():
    adapter = Mock(return_value=RepairListOutput(count=0, items=()))
    executor = _repair_executor(adapter)
    state = GraphState(conversation_id="dup-rt-1")

    repair_invoke(executor, _ctx_provider(), state, "repair_list", {"statuses": (), "limit": 20})

    assert isinstance(state.capability_invocation.prior_fingerprints, frozenset)
    restored = GraphState.from_dict(state.to_dict())
    assert isinstance(restored.capability_invocation.prior_fingerprints, frozenset)
    assert (
        restored.capability_invocation.prior_fingerprints
        == state.capability_invocation.prior_fingerprints
    )


# ---------------------------------------------------------------------------
# Cross-turn: a checkpointed prior fingerprint blocks a resumed turn.
# ---------------------------------------------------------------------------


def test_checkpointed_prior_fingerprint_blocks_resumed_turn():
    adapter = Mock(return_value=RepairListOutput(count=0, items=()))
    executor = _repair_executor(adapter)
    state = GraphState(conversation_id="dup-resume-1")
    payload = {"statuses": (), "limit": 20}

    first = repair_invoke(executor, _ctx_provider(), state, "repair_list", payload)
    assert first.error is None
    assert adapter.call_count == 1

    # A fresh turn resumes with the checkpointed invocation state. The same
    # input must be blocked without re-executing the adapter.
    resumed = GraphState(conversation_id="dup-resume-1")
    resumed.capability_invocation = replace(state.capability_invocation)
    second = repair_invoke(executor, _ctx_provider(), resumed, "repair_list", payload)
    assert second.error.code == "DUPLICATE_INVOCATION"
    assert adapter.call_count == 1


# ---------------------------------------------------------------------------
# 4. Canonical alias and canonical name share one fingerprint identity.
# ---------------------------------------------------------------------------


def test_alias_and_canonical_share_fingerprint_identity():
    # `inspection_create_task` is an alias of `inspection_create`; the same
    # semantic input must resolve to the same canonical fingerprint, so a
    # repeat via the alias is detected as a duplicate of the canonical call.
    adapter = Mock(return_value=InspectionDataOutput(data={"id": "t1"}))
    executor = CapabilityExecutor(
        default_capability_registry(), CapabilityPolicy(), {"inspection_create": adapter}
    )
    ctx = object()

    def make_runtime():
        trusted = RuntimeContext(
            ctx,
            "dup-alias",
            None,
            execution_policy=ExecutionPolicy(allowlist=frozenset({"inspection_create"})),
        )
        return CapabilityRuntimeContext(ctx, None, trusted_runtime=trusted)

    payload = {"title": "消防通道巡查", "description": "巡查消防通道", "point": "1号楼"}

    first = executor.execute(
        "inspection_create",
        payload,
        make_runtime(),
        CapabilityInvocationState(human_confirmed=True),
    )
    assert first.error is None
    assert adapter.call_count == 1

    dup = executor.execute(
        "inspection_create_task",
        payload,
        make_runtime(),
        CapabilityInvocationState(
            human_confirmed=True, prior_fingerprints=frozenset({first.fingerprint})
        ),
    )
    assert dup.error.code == "DUPLICATE_INVOCATION"
    assert adapter.call_count == 1
