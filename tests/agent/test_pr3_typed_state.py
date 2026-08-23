"""PR3 characterization and typed-state contract tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import uuid4

import pytest

from property_agent.agent.capabilities.contracts import CapabilityInvocationState
from property_agent.agent.runtime import ExecutionPolicy, RuntimeContext
from property_agent.agent.state import AgentState
from property_agent.agent.state_codec import CheckpointStateCodec
from property_agent.agent.tools.capability_bridge import invoke_capability
from property_agent.agent.working_state import (
    AnnouncementDraftingState,
    BillingWorkingState,
    EmptyWorkingState,
    InspectionEventWorkingState,
    RepairWorkingState,
)
from property_agent.platform.adapters.api.dependencies import ExecutionSource, RequestContext


def _request_context() -> RequestContext:
    return RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=frozenset({"RESIDENT"}),
        request_id="req-pr3",
        current_house_id=uuid4(),
        bound_house_ids=frozenset(),
        execution_source=ExecutionSource.AGENT,
    )


def test_runtime_context_is_immutable_and_wraps_canonical_authority():
    trusted = _request_context()
    runtime = RuntimeContext.from_request_context(
        trusted,
        conversation_id="conv-pr3",
        execution_policy=ExecutionPolicy(max_steps=4, max_calls=2),
    )

    assert runtime.actor_id == trusted.actor_id
    assert runtime.execution_source == ExecutionSource.AGENT
    assert runtime.execution_policy.max_calls == 2
    with pytest.raises(FrozenInstanceError):
        runtime.conversation_id = "model-controlled"  # type: ignore[misc]


def test_agent_state_has_typed_orchestration_and_domain_ownership():
    state = AgentState(
        conversation_id="conv-pr3",
        domain=RepairWorkingState(description="漏水", location="厨房"),
        capability_invocation=CapabilityInvocationState(calls_made=1),
    )

    assert state.schema_version == CheckpointStateCodec.CURRENT_VERSION
    assert state.domain.description == "漏水"
    assert state.capability_invocation.calls_made == 1
    assert not hasattr(state.capability_invocation, "max_calls")


def test_legacy_bridge_updates_the_agent_state_invocation_owner():
    state = AgentState(conversation_id="conv-progress")
    executor = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(
            error=None,
            output=SimpleNamespace(data={"count": 1}),
            fingerprint="fingerprint-1",
        )
    )

    output = invoke_capability(
        executor,
        lambda _state: _request_context(),
        state,
        "repair_list",
        {},
    )

    assert output == {"count": 1}
    assert state.capability_invocation.selected_capability == "repair_list"
    assert state.capability_invocation.calls_made == 1
    assert state.capability_invocation.prior_fingerprints == {"fingerprint-1"}


@pytest.mark.parametrize(
    "domain",
    [
        EmptyWorkingState(),
        RepairWorkingState(description="漏水"),
        BillingWorkingState(bill_id=None),
        AnnouncementDraftingState(title="停水通知", body="今晚停水", audience={}),
        InspectionEventWorkingState(event_id=uuid4(), expected_version=2),
    ],
)
def test_typed_domain_variants_round_trip(domain):
    codec = CheckpointStateCodec()
    state = AgentState(conversation_id="conv-roundtrip", domain=domain)

    restored = codec.decode(codec.encode(state))

    assert restored.domain == domain


def test_invocation_fingerprints_round_trip_back_to_frozenset():
    codec = CheckpointStateCodec()
    state = AgentState(
        conversation_id="conv-fingerprints",
        capability_invocation=CapabilityInvocationState(
            prior_fingerprints=frozenset({"fingerprint-1"})
        ),
    )

    restored = codec.decode(codec.encode(state))

    assert restored.capability_invocation.prior_fingerprints == frozenset({"fingerprint-1"})


def test_legacy_billing_absence_remains_none_and_authority_is_not_restored_from_checkpoint():
    actor = uuid4()
    payload = {
        "conversation_id": "conv-legacy",
        "actor_id": str(actor),
        "intent": "BILLING",
        "slots": {"action": "consult", "bill_id": ""},
        "trusted_context": {"execution_source": "HUMAN", "fence": 999},
    }

    restored = CheckpointStateCodec().decode(payload)

    assert isinstance(restored.domain, BillingWorkingState)
    assert restored.domain.bill_id is None
    assert restored.actor_id == actor  # compatibility mirror only
    assert not hasattr(restored, "execution_source")


def test_codec_decode_is_pure_and_does_not_mutate_legacy_payload():
    payload = {
        "conversation_id": "conv-pure",
        "intent": "REPAIR",
        "slots": {"description": "漏水", "location": "厨房"},
    }
    before = {
        "conversation_id": payload["conversation_id"],
        "intent": "REPAIR",
        "slots": dict(payload["slots"]),
    }

    CheckpointStateCodec().decode(payload)

    assert payload == before


@pytest.mark.parametrize(
    ("legacy_fields", "expected"),
    [
        ({"intent": "REPAIR", "slots": {"description": "漏水"}}, (0, False, None)),
        ({"missing_slots": ["location"], "requested_slot": "location"}, (0, False, None)),
        ({"_contextual_followup": True}, (0, True, None)),
        (
            {"retry_count": 2, "error": "previous public failure"},
            (2, False, "previous public failure"),
        ),
        ({"pending_action": None, "_interrupt_node": None}, (0, False, None)),
    ],
)
def test_legacy_active_missing_contextual_failed_and_cancelled_shapes_decode(
    legacy_fields, expected
):
    payload = {"conversation_id": "conv-legacy-matrix", **legacy_fields}

    restored = CheckpointStateCodec().decode(payload)

    retry_count, contextual, error = expected
    assert restored.retry_count == retry_count
    assert restored._contextual_followup is contextual
    assert restored.error == error
    if "missing_slots" in legacy_fields:
        assert restored.clarification.missing_inputs == ["location"]
