"""PR2 typed Capability Layer contracts and migrated-path safety tests."""

from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from property_agent.agent.capabilities.adapters.billing import (
    BillingConsultAdapter,
    BillingConsultInput,
    BillingQueryAdapter,
    BillingQueryInput,
)
from property_agent.agent.capabilities.adapters.repair import (
    RepairCreateAdapter,
    RepairCreateInput,
    RepairListAdapter,
)
from property_agent.agent.capabilities.catalog import (
    capability_specs,
    default_capability_registry,
)
from property_agent.agent.capabilities.compatibility import (
    CONTROLLED_READ_GUARD_MAPPING,
    migrated_presentation,
    migrated_tool_levels,
    migrated_tool_slots,
)
from property_agent.agent.capabilities.contracts import (
    ApprovalRequirement,
    CapabilityInvocationState,
    CapabilityPolicyDecision,
    CapabilityRisk,
    CapabilityRuntimeContext,
    PolicyDisposition,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import CapabilityPolicy
from property_agent.agent.capabilities.registry import (
    CapabilityRegistry,
    DuplicateCapabilityError,
    UnknownCapabilityError,
)
from property_agent.agent.policies import TOOL_LEVELS, TOOL_SLOTS


def _runtime(context: object, house_id=None) -> CapabilityRuntimeContext:
    return CapabilityRuntimeContext(context, house_id)


def _executor(adapters, policy=None) -> CapabilityExecutor:
    return CapabilityExecutor(default_capability_registry(), policy or CapabilityPolicy(), adapters)


def test_registry_inventory_lookup_duplicate_and_unknown():
    registry = default_capability_registry()
    assert registry.names() == (
        "billing_consult",
        "billing_query",
        "repair_create",
        "repair_get",
        "repair_list",
    )
    assert registry.get("repair_create").domain == "repair"
    with pytest.raises(UnknownCapabilityError):
        registry.get("missing")
    with pytest.raises(DuplicateCapabilityError):
        CapabilityRegistry([capability_specs()[0], capability_specs()[0]])


@pytest.mark.parametrize(
    "forbidden",
    ["actor_id", "role", "community_id", "house_id", "execution_source", "lease", "fence"],
)
def test_model_cannot_override_trusted_authority(forbidden):
    service = Mock()
    result = _executor({"repair_list": RepairListAdapter(service)}).execute(
        "repair_list", {forbidden: "model-claim"}, _runtime(object())
    )
    assert result.error is not None
    assert result.error.code == "INVALID_CAPABILITY_INPUT"
    service.search.assert_not_called()


def test_typed_input_and_output_are_enforced():
    adapter = Mock(return_value={"count": "not-an-int", "items": []})
    executor = _executor({"repair_list": adapter})
    invalid_input = executor.execute("repair_list", {"limit": 0}, _runtime(object()))
    assert invalid_input.error.code == "INVALID_CAPABILITY_INPUT"
    adapter.assert_not_called()

    invalid_output = executor.execute("repair_list", {"limit": 1}, _runtime(object()))
    assert invalid_output.error.code == "INVALID_CAPABILITY_OUTPUT"
    adapter.assert_called_once()


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (CapabilityInvocationState(allowlist=frozenset()), "CAPABILITY_NOT_ALLOWLISTED"),
        (CapabilityInvocationState(step=1, max_steps=1), "MAX_STEPS_EXCEEDED"),
        (CapabilityInvocationState(calls_made=1, max_calls=1), "EXECUTION_BUDGET_EXCEEDED"),
        (
            CapabilityInvocationState(deadline_monotonic=time.monotonic() - 1),
            "EXECUTION_DEADLINE_EXCEEDED",
        ),
    ],
)
def test_policy_enforces_allowlist_and_execution_bounds(state, reason):
    result = _executor({"repair_list": Mock()}).execute(
        "repair_list", {}, _runtime(object()), state
    )
    assert result.error.code == reason


def test_policy_rejects_duplicate_fingerprint():
    executor = _executor({"repair_list": Mock(return_value={"count": 0, "items": []})})
    first = executor.execute("repair_list", {}, _runtime(object()))
    duplicate = executor.execute(
        "repair_list",
        {},
        _runtime(object()),
        CapabilityInvocationState(prior_fingerprints=frozenset({first.fingerprint})),
    )
    assert duplicate.error.code == "DUPLICATE_INVOCATION"


def test_static_spec_and_dynamic_policy_are_separate():
    spec = default_capability_registry().get("repair_create")
    assert spec.baseline_risk == CapabilityRisk.WRITE_LOW_RISK
    assert not hasattr(spec, "approval_required")

    def emergency_rule(_spec, request, _runtime_context, _invocation):
        if request.urgency == "EMERGENCY":
            return CapabilityPolicyDecision(
                PolicyDisposition.HUMAN_ONLY,
                CapabilityRisk.WRITE_HIGH_RISK,
                ApprovalRequirement.REQUIRED,
                "EMERGENCY_HUMAN_ONLY",
            )
        return None

    policy = CapabilityPolicy({"repair_create": emergency_rule})
    decision = policy.evaluate(
        spec,
        RepairCreateInput(
            description="trapped resident",
            location="lift",
            urgency="EMERGENCY",
            confirmation_token="server-token",
            idempotency_key="key",
        ),
        _runtime(object()),
        CapabilityInvocationState(),
    )
    assert decision.disposition == PolicyDisposition.HUMAN_ONLY
    assert decision.effective_risk == CapabilityRisk.WRITE_HIGH_RISK


def test_write_requires_orchestration_confirmation_before_adapter():
    adapter = Mock()
    payload = {
        "description": "pipe leaking",
        "location": "kitchen",
        "confirmation_token": "server-token",
        "idempotency_key": "key",
    }
    result = _executor({"repair_create": adapter}).execute(
        "repair_create", payload, _runtime(object(), "house")
    )
    assert result.error.code == "HITL_CONFIRMATION_REQUIRED"
    adapter.assert_not_called()


def test_executor_invokes_exactly_one_selected_adapter_and_observes():
    adapter = Mock(return_value={"count": 0, "items": []})
    other = Mock()
    events = []
    executor = CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {"repair_list": adapter, "repair_get": other},
        observe=lambda event, fields: events.append((event, fields)),
    )
    result = executor.execute("repair_list", {}, _runtime(object()))
    assert result.ok
    adapter.assert_called_once()
    other.assert_not_called()
    assert [event for event, _fields in events] == [
        "capability_started",
        "capability_finished",
    ]


def test_real_read_adapter_calls_existing_application_service(service, resident_context):
    executor = _executor({"repair_list": RepairListAdapter(service)})
    result = executor.execute("repair_list", {}, _runtime(resident_context))
    assert result.ok
    assert result.output.count == 0


def test_billing_query_adapter_calls_existing_application_service():
    bill = type(
        "Bill",
        (),
        {
            "bill_id": "B-1",
            "bill_period": "2026-08",
            "total_amount": "88.00",
            "status": "UNPAID",
        },
    )()
    service = Mock()
    service.list_bills.return_value = [bill]
    session = object()
    context = object()
    adapter = BillingQueryAdapter(service, lambda _runtime: session)

    output = adapter(BillingQueryInput(period="2026-08", fee_type="物业费"), _runtime(context))

    service.list_bills.assert_called_once_with(
        context, session, fee_type="PROPERTY", period="2026-08"
    )
    assert output.count == 1
    assert output.items[0].total_amount == "88.00"


def test_billing_write_adapter_forwards_server_approval_to_application_service():
    ticket = type(
        "Ticket",
        (),
        {"id": "C-1", "subject": "账单疑问", "status": "DRAFT", "bill_id": "B-1"},
    )()
    service = Mock()
    service.create_draft.return_value = ticket
    session = object()
    context = object()
    adapter = BillingConsultAdapter(service, lambda _runtime: session)
    request = BillingConsultInput(
        subject="账单疑问",
        description="请人工核对",
        bill_id="B-1",
        confirmation_token="server-token",
        approval_ref="approval-id",
        idempotency_key="agent-key",
    )

    output = adapter(request, _runtime(context))

    service.create_draft.assert_called_once_with(
        context,
        session,
        subject="账单疑问",
        description="请人工核对",
        bill_id="B-1",
        idempotency_key="agent-key",
        confirmation_token="server-token",
        approval_ref="approval-id",
    )
    assert output.consultation.id == "C-1"


def test_real_write_path_preserves_single_execution_and_service_invariants(
    service, harness, ids, resident_context
):
    executor = _executor({"repair_create": RepairCreateAdapter(service)})
    payload = {
        "description": "客厅插座没电",
        "location": "客厅",
        "confirmation_token": "confirmed",
        "idempotency_key": "capability-write-key",
    }
    invocation = CapabilityInvocationState(human_confirmed=True)
    first = executor.execute(
        "repair_create", payload, _runtime(resident_context, ids.house), invocation
    )
    second = executor.execute(
        "repair_create", payload, _runtime(resident_context, ids.house), invocation
    )
    assert first.ok and second.ok
    assert first.output.work_order.id == second.output.work_order.id
    assert len(harness.state.orders) == 1
    assert len(harness.state.idempotency) == 1
    assert len(harness.state.audits) == 1
    assert len(harness.confirmations.consumed) == 1


def test_legacy_metadata_is_derived_from_registry():
    assert {name: TOOL_LEVELS[name] for name in migrated_tool_levels()} == migrated_tool_levels()
    assert {name: TOOL_SLOTS[name] for name in migrated_tool_slots()} == migrated_tool_slots()
    assert migrated_tool_slots() == {
        "billing_consult": ["subject", "description"],
        "billing_query": [],
        "repair_create": ["description", "location"],
        "repair_get": ["work_order_id"],
        "repair_list": [],
    }
    assert migrated_presentation()["repair_create"]["confirmation_title"] == (
        "确认提交这条报修吗？"
    )


def test_controlled_read_guard_inventory_retains_every_security_family():
    assert set(CONTROLLED_READ_GUARD_MAPPING) == {
        "untrusted_argument_guards",
        "tool_allowlist",
        "required_and_supported_arguments",
        "argument_value_bounds",
        "trusted_scope_output_check",
        "max_steps",
        "deadline",
        "duplicate_fingerprint",
        "result_record_bounds",
        "hashed_trace_without_raw_arguments",
        "provider_error_normalization",
    }
    assert all("retained legacy" in value for value in CONTROLLED_READ_GUARD_MAPPING.values())


def test_unknown_capability_is_normalized_without_adapter_call():
    adapter = Mock()
    result = _executor({"repair_list": adapter}).execute(
        "model_invented_tool", {}, _runtime(object())
    )
    assert result.error.code == "UNKNOWN_CAPABILITY"
    adapter.assert_not_called()
