import os
from uuid import uuid4

import pytest
from langgraph.graph.state import CompiledStateGraph

from property_agent.agent.application.langgraph_runtime import (
    LangGraphEngine,
    LangGraphStateCodec,
    build_saver_resource,
)
from property_agent.agent.runtime import PreparedWrite, RuntimeContext
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import synchronize_typed_domain
from property_agent.platform.context import RequestContext


class RecordingSpecialist:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, state, runtime):
        self.calls.append((state.slots["tool"], runtime))
        state.tool_result = {"ok": True}
        state.add_message("assistant", "ok")
        return state


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
    specialist = RecordingSpecialist()
    engine = LangGraphEngine(build_saver_resource(in_memory=True).saver, specialist)
    assert isinstance(engine._graph, CompiledStateGraph)
    result = engine.invoke(
        repair_state(runtime),
        thread_id="pr4-conversation",
        runtime=runtime,
    )
    assert result.done is True
    assert result.runtime_cursor["thread_id"].startswith("lg:pr4-conversation:")
    assert result.runtime_cursor["checkpoint_id"]
    assert specialist.calls[0][0] == "repair_list"


def test_interrupt_resume_is_replay_stable_and_uses_fresh_prepared_write():
    runtime = runtime_fixture()
    specialist = RecordingSpecialist()
    engine = LangGraphEngine(build_saver_resource(in_memory=True).saver, specialist)
    interrupted = engine.invoke(
        repair_state(runtime, create=True),
        thread_id="pr4-conversation",
        runtime=runtime,
    )
    proposal = dict(interrupted.interrupt)
    assert interrupted.done is False
    assert specialist.calls == []

    confirmed_runtime = RuntimeContext.from_request_context(
        runtime.request_context,
        conversation_id=runtime.conversation_id,
        current_house_id=runtime.current_house_id,
        prepared_write=PreparedWrite("server-token", "server-key", "server-approval"),
    )
    resumed = engine.resume(
        "pr4-conversation",
        {"confirmed": True, "confirmation_token": "forged-checkpoint-token"},
        state=interrupted.state,
        runtime=confirmed_runtime,
        runtime_cursor=interrupted.runtime_cursor,
    )
    assert resumed.done is True
    assert len(specialist.calls) == 1
    assert specialist.calls[0][1].prepared_write.confirmation_token == "server-token"
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
        runtime = runtime_fixture()
        engine = LangGraphEngine(resource.saver, RecordingSpecialist())
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
