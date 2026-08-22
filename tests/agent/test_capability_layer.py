"""PR2 typed Capability Layer contracts and migrated-path safety tests."""

from __future__ import annotations

import time
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

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
    CapabilityDomainError,
    CapabilityInvocationState,
    CapabilityPolicyDecision,
    CapabilityRisk,
    CapabilityRuntimeContext,
    CapabilityWriteContext,
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
from property_agent.billing.errors import BillingError


def _runtime(context: object, house_id=None, write=None) -> CapabilityRuntimeContext:
    return CapabilityRuntimeContext(context, house_id, write=write)


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
    [
        "confirmation_token",
        "approval_ref",
        "idempotency_key",
        "actor_id",
        "user_id",
        "role",
        "community_id",
        "house_id",
        "current_house_id",
        "execution_source",
        "lease",
        "fence",
    ],
)
@pytest.mark.parametrize(
    ("name", "semantic_payload"),
    [
        ("repair_create", {"description": "pipe leaking", "location": "kitchen"}),
        ("billing_consult", {"subject": "bill question", "description": "please check"}),
    ],
)
def test_model_cannot_override_server_write_or_trusted_authority(forbidden, name, semantic_payload):
    adapter = Mock()
    result = _executor({name: adapter}).execute(
        name,
        {**semantic_payload, forbidden: "model-claim"},
        _runtime(object(), "house"),
        CapabilityInvocationState(human_confirmed=True),
    )
    assert result.error is not None
    assert result.error.code == "INVALID_CAPABILITY_INPUT"
    adapter.assert_not_called()


def test_write_capability_inputs_contain_only_business_semantics():
    assert set(RepairCreateInput.model_fields) == {"description", "location", "urgency"}
    assert set(BillingConsultInput.model_fields) == {"subject", "description", "bill_id"}


def test_typed_input_and_output_are_enforced():
    secret = "postgresql://secret/internal-output"
    adapter = Mock(return_value={"count": secret, "items": []})
    executor = _executor({"repair_list": adapter})
    invalid_input = executor.execute("repair_list", {"limit": 0}, _runtime(object()))
    assert invalid_input.error.code == "INVALID_CAPABILITY_INPUT"
    adapter.assert_not_called()

    invalid_output = executor.execute("repair_list", {"limit": 1}, _runtime(object()))
    assert invalid_output.error.code == "INVALID_CAPABILITY_OUTPUT"
    assert invalid_output.error.message == "Capability output validation failed."
    assert secret not in str(invalid_output.error.details)
    assert isinstance(invalid_output.error.cause, ValidationError)
    adapter.assert_called_once()


def test_public_capability_domain_error_contract_is_preserved():
    domain_error = CapabilityDomainError(
        "PUBLIC_BUSINESS_ERROR", "Stable public message.", details={"field": "value"}
    )
    result = _executor({"repair_list": Mock(side_effect=domain_error)}).execute(
        "repair_list", {}, _runtime(object())
    )

    assert result.error.code == "PUBLIC_BUSINESS_ERROR"
    assert result.error.message == "Stable public message."
    assert result.error.details == {"field": "value"}
    assert result.error.cause is domain_error


def test_project_public_business_error_contract_is_preserved():
    business_error = BillingError(
        "BILL_NOT_FOUND", "Stable billing message.", 404, {"bill_id": "B-404"}
    )
    result = _executor({"repair_list": Mock(side_effect=business_error)}).execute(
        "repair_list", {}, _runtime(object())
    )

    assert result.error.code == "BILL_NOT_FOUND"
    assert result.error.message == "Stable billing message."
    assert result.error.details == {"bill_id": "B-404"}
    assert result.error.cause is business_error


def test_unexpected_adapter_error_is_sanitized_but_retains_internal_cause():
    secret = "postgresql://secret-user:secret-pass@db/internal SQL SELECT credentials"
    internal_error = RuntimeError(secret)
    result = _executor({"repair_list": Mock(side_effect=internal_error)}).execute(
        "repair_list", {}, _runtime(object())
    )

    assert result.error.code == "CAPABILITY_EXECUTION_FAILED"
    assert result.error.message == "Capability execution failed."
    assert result.error.details == {}
    assert secret not in f"{result.error.code}{result.error.message}{result.error.details}"
    assert result.error.cause is internal_error
    assert secret in str(result.error.cause)


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


def test_observer_failure_before_adapter_does_not_block_execution():
    adapter = Mock(return_value={"count": 0, "items": []})

    def observe(event, _fields):
        if event == "capability_started":
            raise RuntimeError("telemetry sink unavailable")

    executor = CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {"repair_list": adapter},
        observe=observe,
    )

    result = executor.execute("repair_list", {}, _runtime(object()))

    assert result.ok
    adapter.assert_called_once()


def test_observer_failure_after_success_does_not_replace_result():
    def observe(event, _fields):
        if event == "capability_finished":
            raise RuntimeError("telemetry sink unavailable")

    result = CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {"repair_list": Mock(return_value={"count": 0, "items": []})},
        observe=observe,
    ).execute("repair_list", {}, _runtime(object()))

    assert result.ok
    assert result.output.count == 0


def test_observer_failure_while_recording_error_preserves_capability_error():
    domain_error = CapabilityDomainError("PUBLIC_FAILURE", "Stable failure.")

    def observe(event, _fields):
        if event == "capability_failed":
            raise RuntimeError("telemetry sink unavailable")

    result = CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {"repair_list": Mock(side_effect=domain_error)},
        observe=observe,
    ).execute("repair_list", {}, _runtime(object()))

    assert result.error.code == "PUBLIC_FAILURE"
    assert result.error.message == "Stable failure."
    assert result.error.cause is domain_error


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


@pytest.mark.parametrize(
    "payload",
    [
        {"query_type": "detail"},
        {"query_type": "rule"},
    ],
)
def test_billing_query_shape_errors_fail_input_validation_before_adapter(payload):
    adapter = Mock()

    result = _executor({"billing_query": adapter}).execute(
        "billing_query", payload, _runtime(object())
    )

    assert result.error.code == "INVALID_CAPABILITY_INPUT"
    adapter.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"query_type": "list"},
        {"query_type": "list", "period": "2026-08", "fee_type": "PROPERTY"},
        {"query_type": "detail", "bill_id": "B-1"},
        {"query_type": "rule", "fee_type": "PROPERTY"},
    ],
)
def test_billing_query_valid_shapes_remain_accepted(payload):
    assert BillingQueryInput.model_validate(payload).query_type == payload["query_type"]


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
    )
    write = CapabilityWriteContext(
        confirmation_token="server-token",
        approval_ref="approval-id",
        idempotency_key="agent-key",
    )

    output = adapter(request, _runtime(context, write=write))

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
    }
    invocation = CapabilityInvocationState(human_confirmed=True)
    runtime = _runtime(
        resident_context,
        ids.house,
        CapabilityWriteContext(
            confirmation_token="confirmed", idempotency_key="capability-write-key"
        ),
    )
    first = executor.execute("repair_create", payload, runtime, invocation)
    second = executor.execute("repair_create", payload, runtime, invocation)
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
