"""Focused regressions for the final PR3 architecture contract review."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from property_agent.agent.application.domain_continuation import prepare_start_state
from property_agent.agent.capabilities.adapters.inspection import (
    InspectionAdapter,
    InspectionCreateInput,
    InspectionDataOutput,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import (
    CapabilityInvocationState,
    CapabilityRuntimeContext,
    CapabilityWriteContext,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import CapabilityPolicy, default_capability_policy
from property_agent.agent.capabilities.registry import CapabilityRegistry
from property_agent.agent.nodes.execute_tool import execute_tool_node
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.selector_context import activate_selector_context
from property_agent.agent.state import AgentState
from property_agent.agent.state_codec import CheckpointStateCodec
from property_agent.agent.subgraphs.announcement import select_announcement_tool
from property_agent.agent.subgraphs.inspection import select_inspection_tool
from property_agent.agent.tools.capability_bridge import invoke_capability
from property_agent.agent.working_state import (
    AnnouncementDraftingState,
    AnnouncementPublishState,
    AnnouncementQueryState,
    BillingWorkingState,
    DomainIntentMismatchError,
    EmptyWorkingState,
    InspectionEventWorkingState,
    InspectionTaskWorkingState,
    RepairWorkingState,
)
from property_agent.inspection.adapters.api.dependencies import to_inspection_context
from property_agent.inspection.domain.enums import EventRiskLevel, EventType
from property_agent.inspection.domain.errors import BusinessError as InspectionBusinessError
from property_agent.platform.application.agent_write_authority import consume_agent_write
from property_agent.platform.application.confirm_params import derive_confirmation_params
from property_agent.platform.context import AgentLeaseContext, ExecutionSource, RequestContext
from property_agent.platform.domain.exceptions import TrustedExecutionOriginError


def _platform_context(*, roles=frozenset({"MANAGER"})) -> RequestContext:
    house = uuid4()
    lease = AgentLeaseContext(
        thread_id="conv-contract",
        run_id=uuid4(),
        fence=7,
        lease_until=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    return RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=roles,
        request_id="req-contract",
        current_house_id=house,
        bound_house_ids=frozenset({house}),
        agent_lease=lease,
        execution_source=ExecutionSource.AGENT,
    )


class _ConfirmationRequired(RuntimeError):
    pass


@pytest.mark.parametrize("source", [None, "HUMAN", "AGENT", "UNKNOWN"])
def test_agent_write_authority_rejects_missing_or_invalid_origin_without_inference(source):
    context = SimpleNamespace(
        actor_id=uuid4(),
        request_id="req-origin",
        confirmation_token="looks-valid",
        approval_ref="approval",
        agent_lease=object(),
        idempotency_key="key",
    )
    if source is not None:
        context.execution_source = source
    command = SimpleNamespace(confirmation_token="token", approval_ref="approval")

    with pytest.raises(TrustedExecutionOriginError):
        consume_agent_write(
            SimpleNamespace(confirmations=SimpleNamespace(consume=lambda **_: None)),
            context,
            command,
            "WRITE",
            {},
            _ConfirmationRequired,
        )


def test_agent_write_authority_preserves_explicit_human_and_agent_semantics():
    consumed = []
    uow = SimpleNamespace(confirmations=SimpleNamespace(consume=lambda **kw: consumed.append(kw)))
    command = SimpleNamespace(confirmation_token=None, approval_ref=None)
    human = SimpleNamespace(
        execution_source=ExecutionSource.HUMAN,
        actor_id=uuid4(),
        request_id="human",
    )
    consume_agent_write(uow, human, command, "WRITE", {}, _ConfirmationRequired)
    assert consumed == []

    agent = SimpleNamespace(
        execution_source=ExecutionSource.AGENT,
        actor_id=uuid4(),
        request_id="agent",
    )
    with pytest.raises(_ConfirmationRequired):
        consume_agent_write(uow, agent, command, "WRITE", {}, _ConfirmationRequired)


def test_inspection_runtime_root_and_projection_preserve_canonical_authority():
    canonical = _platform_context(roles=frozenset({"MANAGER", "SECURITY_GUARD"}))
    trusted = RuntimeContext.from_request_context(canonical, conversation_id="conv-contract")
    runtime = CapabilityRuntimeContext(
        canonical,
        canonical.current_house_id,
        trusted_runtime=trusted,
        inspection_context_projector=lambda context: to_inspection_context(
            context, context.request_id
        ),
    )
    projected = to_inspection_context(canonical, canonical.request_id)

    assert trusted.request_context is canonical
    assert runtime.request_context is canonical
    assert projected.actor_id == canonical.actor_id
    assert projected.community_id == canonical.community_id
    assert projected.execution_source is ExecutionSource.AGENT
    assert projected.agent_lease == canonical.agent_lease


def test_inspection_projection_cannot_override_canonical_values():
    canonical = _platform_context()
    executor = CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {
            "inspection_list": InspectionAdapter(
                SimpleNamespace(), SimpleNamespace(), "inspection_list"
            )
        },
    )
    runtime = CapabilityRuntimeContext(
        canonical,
        inspection_context_projector=lambda context: SimpleNamespace(
            actor_id=uuid4(),
            community_id=context.community_id,
            execution_source=context.execution_source,
            agent_lease=context.agent_lease,
            roles=frozenset(),
        ),
    )

    result = executor.execute("inspection_list", {}, runtime)

    assert result.error is not None
    assert result.error.code == "CAPABILITY_EXECUTION_FAILED"


@pytest.mark.parametrize(
    "domain",
    [
        EmptyWorkingState(),
        RepairWorkingState(location="厨房"),
        BillingWorkingState(bill_id=None),
        AnnouncementQueryState(topic="停水"),
        AnnouncementDraftingState(title="停水通知"),
        AnnouncementPublishState(announcement_id=uuid4()),
        InspectionTaskWorkingState(title="夜巡"),
        InspectionEventWorkingState(description="消防通道堵塞"),
    ],
)
def test_every_admitted_typed_domain_variant_round_trips(domain):
    state = AgentState(conversation_id="conv-variants", domain=domain)
    restored = CheckpointStateCodec().decode(CheckpointStateCodec().encode(state))
    assert restored.domain == domain


def test_v2_typed_domain_wins_over_conflicting_legacy_projection():
    codec = CheckpointStateCodec()
    state = AgentState(
        conversation_id="conv-typed-wins",
        intent="REPAIR",
        domain=RepairWorkingState(location="厨房"),
        slots={"location": "卫生间"},
    )
    restored = codec.decode(codec.encode(state))
    assert restored.domain.location == "厨房"
    assert restored.slots["location"] == "厨房"


def test_v2_rejects_cross_domain_intent_conflict():
    state = AgentState(
        conversation_id="conv-invalid",
        intent="BILLING",
        domain=RepairWorkingState(location="厨房"),
    )
    with pytest.raises(DomainIntentMismatchError):
        CheckpointStateCodec().encode(state)


def test_new_turn_has_typed_inspection_state_before_checkpoint_encode():
    context = _platform_context(roles=frozenset({"SECURITY_GUARD"}))
    prepared = prepare_start_state(
        conversation_id="conv-new-turn",
        context=context,
        current_house_id=context.current_house_id,
        previous=None,
        user_text="巡检发现消防通道堵塞，需要上报异常",
        slots={"location": "1栋", "description": "消防通道堵塞"},
    )
    assert isinstance(prepared.state.domain, InspectionEventWorkingState)


def test_domain_continuation_uses_typed_repair_state_not_stale_slots():
    context = _platform_context(roles=frozenset({"RESIDENT"}))
    previous = AgentState(
        conversation_id="conv-continuation",
        current_house_id=context.current_house_id,
        intent="REPAIR",
        domain=RepairWorkingState(description="漏水", location="厨房"),
        slots={"description": "漏水", "location": "卫生间"},
    )
    prepared = prepare_start_state(
        conversation_id=previous.conversation_id,
        context=context,
        current_house_id=context.current_house_id,
        previous=previous,
        user_text="那个",
        slots=None,
    )
    assert isinstance(prepared.state.domain, RepairWorkingState)
    assert prepared.state.domain.location == "厨房"


def test_migrated_selectors_read_typed_domain_and_ignore_conflicting_slots():
    activate_selector_context(SimpleNamespace(roles={"MANAGER"}))
    announcement = AgentState(
        conversation_id="conv-ann",
        intent="ANNOUNCEMENT",
        domain=AnnouncementQueryState(),
        slots={"action": "publish", "title": "tampered", "body": "tampered"},
    )
    inspection = AgentState(
        conversation_id="conv-inspection",
        intent="INSPECTION",
        domain=InspectionTaskWorkingState(action="query"),
        slots={"action": "report_event"},
    )
    assert select_announcement_tool(announcement) == "announcement_list"
    assert select_inspection_tool(inspection) == "inspection_list"


def test_slots_roles_cannot_elevate_resident_announcement_authority():
    activate_selector_context(SimpleNamespace(roles={"RESIDENT"}))
    state = AgentState(
        conversation_id="conv-role-attack",
        intent="ANNOUNCEMENT",
        domain=AnnouncementDraftingState(action="create", title="通知", body="正文", audience={}),
        slots={"roles": ["MANAGER"]},
    )
    assert select_announcement_tool(state) == "announcement_list"
    assert "roles" not in CheckpointStateCodec().encode(state)["slots"]


@pytest.mark.parametrize(
    "business_error",
    [
        InspectionBusinessError("FORBIDDEN", "forbidden", 403),
        InspectionBusinessError("VERSION_CONFLICT", "version", 409, {"current_version": 2}),
        InspectionBusinessError("VALIDATION_ERROR", "invalid", 422, {"field": "title"}),
    ],
)
def test_inspection_business_errors_keep_public_capability_contract(business_error):
    service = SimpleNamespace(
        create_task=lambda *_args, **_kwargs: (_ for _ in ()).throw(business_error)
    )
    executor = CapabilityExecutor(
        default_capability_registry(),
        CapabilityPolicy(),
        {"inspection_create": InspectionAdapter(service, SimpleNamespace(), "inspection_create")},
    )
    canonical = _platform_context()
    result = executor.execute(
        "inspection_create",
        {"title": "夜巡", "description": "检查", "point": "1栋"},
        CapabilityRuntimeContext(
            canonical,
            write=CapabilityWriteContext("token", "key"),
            inspection_context_projector=lambda context: to_inspection_context(
                context, context.request_id
            ),
        ),
        CapabilityInvocationState(human_confirmed=True),
    )
    assert result.error is not None
    assert result.error.code == business_error.code
    assert result.error.kind == "business"


def test_direct_security_capability_enforces_shared_high_risk_floor():
    captured = []
    event = SimpleNamespace(
        id=uuid4(),
        status="REPORTED",
        version=1,
        ai_pending_confirm=False,
        report_source="AI",
        event_type=EventType.GAS_LEAK,
        risk_level=EventRiskLevel.HIGH_RISK,
        location="1栋",
    )
    service = SimpleNamespace(
        create_event=lambda command, _context, **_kwargs: captured.append(command) or event
    )
    executor = CapabilityExecutor(
        default_capability_registry(),
        default_capability_policy(),
        {
            "security_event_create": InspectionAdapter(
                SimpleNamespace(), service, "security_event_create"
            )
        },
    )
    canonical = _platform_context()
    result = executor.execute(
        "security_event_create",
        {
            "event_type": "OTHER",
            "risk_level": "LOW",
            "location": "1栋",
            "description": "闻到燃气泄漏并伴有燃气味",
        },
        CapabilityRuntimeContext(
            canonical,
            write=CapabilityWriteContext("token", "key"),
            inspection_context_projector=lambda context: to_inspection_context(
                context, context.request_id
            ),
        ),
        CapabilityInvocationState(human_confirmed=True),
    )
    assert result.ok
    assert result.decision.effective_risk.value == "write-high-risk"
    assert captured[0].event_type is EventType.GAS_LEAK
    assert captured[0].risk_level is EventRiskLevel.HIGH_RISK
    assert result.output.data["handover_required"] is True

    confirmation_state = AgentState(
        conversation_id="conv-risk-confirm",
        pending_action={"tool": "security_event_create"},
        slots={
            "event_type": "OTHER",
            "risk_level": "LOW",
            "location": "1栋",
            "description": "闻到燃气泄漏并伴有燃气味",
        },
    )
    action, parameters = derive_confirmation_params(
        confirmation_state,
        announcement_service=None,
    )
    assert action == "SECURITY_EVENT_CREATE"
    assert parameters == {
        "event_type": captured[0].event_type.value,
        "risk_level": captured[0].risk_level.value,
        "location": captured[0].location,
    }


def test_legacy_alias_resolves_to_one_canonical_capability_identity():
    registry = default_capability_registry()
    calls = []
    executor = CapabilityExecutor(
        registry,
        CapabilityPolicy(),
        {
            "inspection_create": lambda _request, _runtime: (
                calls.append(1) or InspectionDataOutput(data={})
            )
        },
    )
    canonical = _platform_context()
    result = executor.execute(
        "inspection_create_task",
        InspectionCreateInput(title="夜巡", description="检查", point="1栋"),
        CapabilityRuntimeContext(canonical),
        CapabilityInvocationState(human_confirmed=True),
    )
    assert "inspection_create_task" not in registry.names()
    assert registry.get("inspection_create_task").name == "inspection_create"
    assert result.capability == "inspection_create"
    assert calls == [1]


def test_repair_confirmation_derives_category_from_exact_pending_description():
    house_id = uuid4()
    state = AgentState(
        conversation_id="conv-repair-confirm",
        current_house_id=house_id,
        pending_action={
            "tool": "repair_create",
            "params": {
                "description": "厨房水管漏水",
                "location": "厨房",
                "urgency": "NORMAL",
            },
        },
        slots={"category": "OTHER", "description": "stale legacy description"},
    )

    action, parameters = derive_confirmation_params(state, announcement_service=None)

    assert action == "CREATE_WORK_ORDER"
    assert parameters["house_id"] == house_id
    assert parameters["category"].value == "WATER_PLUMBING"
    assert parameters["description"] == "厨房水管漏水"


def test_legacy_graph_never_reexposes_internal_capability_cause():
    secret = "postgres password=secret-value"
    executor = CapabilityExecutor(
        CapabilityRegistry([default_capability_registry().get("repair_list")]),
        CapabilityPolicy(),
        {"repair_list": lambda *_: (_ for _ in ()).throw(RuntimeError(secret))},
    )
    canonical = _platform_context(roles=frozenset({"RESIDENT"}))
    state = AgentState(conversation_id="conv-secret")

    def tool(current):
        return invoke_capability(executor, lambda _state: canonical, current, "repair_list", {})

    execute_tool_node({"repair_list": tool})(
        AgentState(
            conversation_id=state.conversation_id,
            pending_action={"tool": "repair_list"},
        )
    )
    exposed = AgentState(
        conversation_id="conv-secret-exposed",
        pending_action={"tool": "repair_list"},
    )
    execute_tool_node({"repair_list": tool})(exposed)
    assert exposed.error == "CAPABILITY_EXECUTION_FAILED: Capability execution failed."
    assert "secret-value" not in exposed.error
    assert "password" not in exposed.error
