import os
from uuid import uuid4

import pytest
from langgraph.graph.state import CompiledStateGraph

from property_agent.agent.application.langgraph_runtime import (
    LangGraphEngine,
    LangGraphStateCodec,
    build_saver_resource,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import default_capability_policy
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.runtime import PreparedWrite, RuntimeContext
from property_agent.agent.specialists import (
    AnnouncementSpecialist,
    BillingSpecialist,
    InspectionSpecialist,
    RepairSpecialist,
)
from property_agent.agent.specialists.supervisor import Supervisor
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import synchronize_typed_domain
from property_agent.platform.context import RequestContext
from tests.agent.pr5_semantic_fakes import proposal, step


class RepairProviderContractGateway:
    def propose_plan(self, text, *, history, trusted_context):
        del history, trusted_context
        if text == "查询报修记录":
            return proposal(step("repair-read", "repair", "repair_list", "查询报修记录"))
        return proposal(
            step(
                "repair-create",
                "repair",
                "repair_create",
                "提交水管漏水报修",
                parameters={"description": "水管漏水", "location": "厨房", "appointment_at": None},
            )
        )


def _supervisor(adapters):
    executor = CapabilityExecutor(
        default_capability_registry(), default_capability_policy(), adapters
    )
    specialists = (
        RepairSpecialist(executor),
        BillingSpecialist(executor),
        AnnouncementSpecialist(executor),
        InspectionSpecialist(executor),
    )
    return Supervisor(
        SupervisorPlanner(RepairProviderContractGateway()),
        {specialist.name: specialist for specialist in specialists},
    )


def runtime_fixture():
    house_id = uuid4()
    request = RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=frozenset({"RESIDENT"}),
        bound_house_ids=frozenset({house_id}),
        request_id="pr4-test",
    )
    runtime = RuntimeContext.from_request_context(
        request, conversation_id="pr4-conversation", current_house_id=house_id
    )
    return runtime


def repair_state(runtime, *, create=False):
    slots = {"user_text": "查询报修记录", "action": "list"}
    if create:
        slots = {
            "user_text": "我要报修水管漏水",
            "action": "create",
            "description": "水管漏水",
            "location": "厨房",
            "appointment_at": None,
        }
    state = GraphState(
        "pr4-conversation",
        actor_id=runtime.actor_id,
        community_id=runtime.community_id,
        current_house_id=runtime.current_house_id,
        intent="REPAIR",
        slots=slots,
    )
    synchronize_typed_domain(state)
    return state


def test_official_state_graph_captures_exact_current_execution_cursor():
    runtime = runtime_fixture()
    calls = []
    supervisor = _supervisor(
        {
            "repair_list": lambda _request, _runtime: (
                calls.append("repair_list") or {"count": 0, "items": ()}
            )
        }
    )
    engine = LangGraphEngine(build_saver_resource(in_memory=True).saver, supervisor)
    assert isinstance(engine._graph, CompiledStateGraph)
    result = engine.invoke(
        repair_state(runtime),
        thread_id="pr4-conversation",
        runtime=runtime,
    )
    assert result.done is True
    assert result.runtime_cursor["thread_id"].startswith("lg:pr4-conversation:")
    assert result.runtime_cursor["checkpoint_id"]
    assert calls == ["repair_list"]


def test_interrupt_resume_is_replay_stable_and_uses_fresh_prepared_write():
    runtime = runtime_fixture()
    calls = []

    def create(_request, capability_runtime):
        calls.append(capability_runtime.trusted_runtime)
        return {
            "work_order": {
                "id": str(uuid4()),
                "status": "PENDING",
                "category": "WATER_PLUMBING",
                "urgency": "NORMAL",
            },
            "idempotency_key": "server-key",
        }

    engine = LangGraphEngine(
        build_saver_resource(in_memory=True).saver,
        _supervisor({"repair_create": create}),
    )
    interrupted = engine.invoke(
        repair_state(runtime, create=True),
        thread_id="pr4-conversation",
        runtime=runtime,
    )
    proposal = dict(interrupted.interrupt)
    assert interrupted.done is False
    assert calls == []

    confirmed_runtime = RuntimeContext.from_request_context(
        runtime.request_context,
        conversation_id=runtime.conversation_id,
        current_house_id=runtime.current_house_id,
        prepared_write=PreparedWrite(
            "server-token",
            "server-key",
            "server-approval",
            capability=interrupted.state.pending_action["tool"],
            params_hash=interrupted.state.pending_action["params_hash"],
            plan_id=interrupted.state.pending_action["plan_id"],
            plan_step_id=interrupted.state.pending_action["plan_step_id"],
        ),
    )
    resumed = engine.resume(
        "pr4-conversation",
        {"confirmed": True, "confirmation_token": "forged-checkpoint-token"},
        state=interrupted.state,
        runtime=confirmed_runtime,
        runtime_cursor=interrupted.runtime_cursor,
    )
    assert resumed.done is True
    assert len(calls) == 1
    assert calls[0].prepared_write.confirmation_token == "server-token"
    assert interrupted.interrupt == proposal
    assert resumed.state.confirmation_token is None
    assert resumed.state.approval_ref is None


def test_v2_codec_drops_authority_and_rejects_custom_objects():
    runtime = runtime_fixture()
    state = repair_state(runtime)
    state.confirmation_token = "secret"
    state.approval_ref = "approval"
    state.trusted_context = {"roles": ["ADMIN"]}
    encoded = LangGraphStateCodec.encode(state)["agent_state"]
    assert "actor_id" not in encoded
    assert "community_id" not in encoded
    assert "confirmation_token" not in encoded
    assert "approval_ref" not in encoded
    assert "trusted_context" not in encoded

    state.slots["unsafe"] = object()
    with pytest.raises(TypeError, match="non-primitive"):
        LangGraphStateCodec.encode(state)


@pytest.mark.postgres
def test_postgres_saver_sync_cursor_is_exactly_resolvable():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is required")
    resource = build_saver_resource(dsn=url.replace("postgresql+psycopg", "postgresql"))
    try:
        resource.saver.setup()
        runtime = runtime_fixture()
        engine = LangGraphEngine(
            resource.saver,
            _supervisor({"repair_list": lambda _request, _runtime: {"count": 0, "items": ()}}),
        )
        result = engine.invoke(
            repair_state(runtime), thread_id=f"pr4-pg-{uuid4()}", runtime=runtime
        )
        assert result.done is True
        exact = resource.saver.get_tuple({"configurable": result.runtime_cursor})
        assert exact is not None
        assert (
            exact.config["configurable"]["checkpoint_id"] == result.runtime_cursor["checkpoint_id"]
        )
    finally:
        resource.close()
