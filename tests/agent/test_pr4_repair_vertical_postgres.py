"""Production-shaped PR4 v2 Repair vertical acceptance on real PostgreSQL."""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from property_agent.agent.adapters.api.presentation import turn_data, wire_events
from property_agent.agent.adapters.api.stream_delivery import BoundedStreamBridge
from property_agent.agent.application.composition import build_supervisor, close_runtime_resources
from property_agent.agent.application.errors import AgentSessionError, AgentSessionErrorCode
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.application.langgraph_runtime import (
    LangGraphEngine,
    build_saver_resource,
)
from property_agent.agent.application.stream_execution import BoundedStreamExecutionRegistry
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.models import AgentActionApprovalModel
from property_agent.agent.infrastructure.run_lease import RunLeaseService
from property_agent.agent.stream_events import StreamEventKind
from property_agent.config import settings
from property_agent.platform.application.approval_service import ApprovalService
from property_agent.platform.container import build_agent_runner, build_production_container
from property_agent.platform.context import RequestContext
from property_agent.platform.infrastructure.orm_models import (
    Base,
    CommunityModel,
    HouseModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.repair.infrastructure.models import WorkOrderModel
from property_agent.repair.infrastructure.uow import SqlAlchemyRepairUnitOfWork
from tests.agent.pr5_semantic_fakes import proposal, step

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@dataclass
class VerticalRuntime:
    app: FastAPI
    sessions: Any
    facade: AgentRuntimeFacadeImpl
    context: RequestContext
    house_id: Any


def _seed_identity(sessions: Any) -> tuple[RequestContext, Any]:
    community_id, house_id, actor_id = uuid4(), uuid4(), uuid4()
    with sessions() as session:
        session.add_all(
            [
                CommunityModel(id=community_id, name=f"PR4-{community_id}"),
                HouseModel(
                    id=house_id,
                    community_id=community_id,
                    building="4",
                    unit="1",
                    room_no="402",
                ),
                UserModel(
                    id=actor_id,
                    community_id=community_id,
                    username=f"pr4-{actor_id}",
                    display_name="PR4 Resident",
                    password_hash="not-used-by-agent-test",
                ),
                UserRoleModel(user_id=actor_id, role="RESIDENT"),
                UserHouseBindingModel(user_id=actor_id, house_id=house_id),
            ]
        )
        session.commit()
    return (
        RequestContext(
            actor_id=actor_id,
            community_id=community_id,
            roles=frozenset({"RESIDENT"}),
            request_id="pr4-vertical",
            current_house_id=house_id,
            bound_house_ids=frozenset({house_id}),
        ),
        house_id,
    )


@pytest.fixture
def vertical_runtime(monkeypatch: pytest.MonkeyPatch) -> VerticalRuntime:
    if not POSTGRES_URL:
        pytest.skip("TEST_POSTGRES_URL is required")
    monkeypatch.setattr(settings, "env", "test-postgres")
    monkeypatch.setattr(settings, "database_url", POSTGRES_URL)
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    context, house_id = _seed_identity(sessions)
    app = FastAPI()
    build_production_container(app)
    app.state.langgraph_saver_resource.saver.setup()
    app.state.agent_model_gateway = RepairSemanticPlanningGateway()
    app.state.agent_lifecycle._engine = LangGraphEngine(
        app.state.langgraph_saver_resource.saver,
        build_supervisor(app),
    )
    facade = app.state.agent_runner
    yield VerticalRuntime(app, sessions, facade, context, house_id)
    app.state.agent_stream_executions.shutdown(2)
    close_runtime_resources(app)
    Base.metadata.drop_all(engine)
    engine.dispose()


class RepairSemanticPlanningGateway:
    def propose_plan(self, text, *, history, trusted_context):
        del text, history, trusted_context
        return proposal(
            step(
                "repair-create",
                "repair",
                "repair_create",
                "提交厨房水管漏水报修",
            )
        )


def _start(facade: Any, context: RequestContext, house_id: Any, conversation_id: str):
    turn = facade.start(
        conversation_id=conversation_id,
        context=context,
        user_text="我要报修厨房水管漏水",
        house_id=house_id,
        slots={
            "action": "create",
            "category": "WATER_PLUMBING",
            "description": "厨房水管漏水",
            "location": "厨房",
            "urgency": "NORMAL",
        },
    )
    return turn, turn_data(turn)


def _reconstruct(runtime: VerticalRuntime) -> tuple[AgentRuntimeFacadeImpl, Any]:
    close_runtime_resources(runtime.app)
    approvals = ApprovalService(runtime.sessions)
    lifecycle = build_agent_runner(
        runtime.app,
        approval_service=approvals,
        run_lease_service=RunLeaseService(runtime.sessions),
    )
    runtime.app.state.agent_model_gateway = RepairSemanticPlanningGateway()
    resource = build_saver_resource(
        dsn=str(POSTGRES_URL).replace("postgresql+psycopg", "postgresql")
    )
    runtime.app.state.langgraph_saver_resource = resource
    engine = LangGraphEngine(
        resource.saver,
        build_supervisor(runtime.app),
    )
    lifecycle._engine = engine
    facade = AgentRuntimeFacadeImpl(lifecycle=lifecycle)
    return facade, resource


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(item) for item in value.values()), set())
    if isinstance(value, (list, tuple)):
        return set().union(*(_nested_keys(item) for item in value), set())
    return set()


@pytest.mark.postgres
def test_v2_repair_vertical_restart_confirm_and_cancel_real_postgres(
    vertical_runtime: VerticalRuntime,
) -> None:
    runtime = vertical_runtime
    conversation_id = f"pr4-vertical-{uuid4()}"
    pending_turn, pending = _start(
        runtime.facade, runtime.context, runtime.house_id, conversation_id
    )
    card = pending["pending_confirmation"]
    assert card["tool"] == "repair_create"
    assert card["action_hash"]
    assert pending["operation_level"] == "write-low-risk"
    assert pending_turn.interrupt["type"] == "confirmation"

    accepted = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(conversation_id)
    assert accepted is not None and accepted.runtime_cursor is not None
    assert accepted.pending_confirm is True
    assert (
        runtime.app.state.langgraph_saver_resource.saver.get_tuple(
            {"configurable": accepted.runtime_cursor.to_dict()}
        )
        is not None
    )
    with runtime.sessions() as session:
        assert session.execute(select(WorkOrderModel)).scalars().all() == []

    restarted, resource = _reconstruct(runtime)
    completed_turn = restarted.resume(
        conversation_id=conversation_id,
        context=runtime.context,
        confirmed=True,
        action_hash=card["action_hash"],
    )
    completed = turn_data(completed_turn)
    assert completed["facts"]["work_order"]["id"]
    assert completed["operation_level"] == "write-low-risk"

    accepted_after = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(conversation_id)
    assert accepted_after is not None and accepted_after.runtime_cursor is not None
    exact = resource.saver.get_tuple({"configurable": accepted_after.runtime_cursor.to_dict()})
    assert exact is not None
    assert not _nested_keys(exact.checkpoint).intersection(
        {"confirmation_token", "approval_ref", "lease", "fence"}
    )
    with runtime.sessions() as session:
        orders = session.execute(select(WorkOrderModel)).scalars().all()
        approval = session.execute(
            select(AgentActionApprovalModel).where(
                AgentActionApprovalModel.conversation_id == conversation_id
            )
        ).scalar_one()
        assert len(orders) == 1
        assert approval.action == "CREATE_WORK_ORDER"
        assert approval.status == "CONSUMED"
        assert str(orders[0].id) == completed["facts"]["work_order"]["id"]

    with pytest.raises(AgentSessionError) as replay:
        restarted.resume(
            conversation_id=conversation_id,
            context=runtime.context,
            confirmed=True,
            action_hash=card["action_hash"],
        )
    assert replay.value.code == AgentSessionErrorCode.NOTHING_PENDING

    cancel_id = f"pr4-cancel-{uuid4()}"
    _, cancel_pending = _start(restarted, runtime.context, runtime.house_id, cancel_id)
    cancelled = restarted.resume(
        conversation_id=cancel_id,
        context=runtime.context,
        confirmed=False,
        action_hash=cancel_pending["pending_confirmation"]["action_hash"],
    )
    assert turn_data(cancelled)["done"] is True
    with runtime.sessions() as session:
        assert len(session.execute(select(WorkOrderModel)).scalars().all()) == 1


class _RejectAcceptedPublication:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def publish_accepted(self, *args, **kwargs):
        raise RuntimeError("forced accepted-head publication failure")


class _RecordAcceptedPublication:
    def __init__(self, delegate: Any, order: list[str]) -> None:
        self._delegate = delegate
        self._order = order

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def publish_accepted(self, *args, **kwargs):
        result = self._delegate.publish_accepted(*args, **kwargs)
        self._order.append("accepted_head")
        return result


@pytest.mark.postgres
def test_v2_stream_checkpoint_success_then_accepted_failure_has_no_public_success(
    vertical_runtime: VerticalRuntime,
) -> None:
    runtime = vertical_runtime
    lifecycle = runtime.app.state.agent_lifecycle
    original = lifecycle._checkpointer
    lifecycle._checkpointer = _RejectAcceptedPublication(original)
    observation = runtime.app.state.agent_observability
    registry = BoundedStreamExecutionRegistry(1, observation)
    conversation_id = f"pr7a-orphan-{uuid4()}"
    try:
        bridge = BoundedStreamBridge(
            lambda: runtime.facade.stream_start(
                conversation_id=conversation_id,
                context=runtime.context,
                user_text="我要报修厨房水管漏水",
                house_id=runtime.house_id,
                slots={
                    "action": "create",
                    "category": "WATER_PLUMBING",
                    "description": "厨房水管漏水",
                    "location": "厨房",
                    "urgency": "NORMAL",
                },
            ),
            registry=registry,
            observability=observation,
        )
        events = list(bridge.events())
    finally:
        lifecycle._checkpointer = original
        registry.shutdown(2)

    public_names = [name for event in events for name, _payload in wire_events(event)]
    assert events[-1].kind is StreamEventKind.FAILED
    assert "turn" not in public_names
    assert "done" not in public_names
    assert original.load_accepted(conversation_id) is None
    checkpoint_outcomes = [
        point.attributes.get("outcome")
        for point in observation.points
        if point.name == "agent_checkpoint_persist_total"
        and point.attributes.get("runtime") == "v2"
    ]
    accepted_outcomes = [
        point.attributes.get("outcome")
        for point in observation.points
        if point.name == "agent_accepted_head_publish_total"
    ]
    assert "COMPLETED" in checkpoint_outcomes
    assert accepted_outcomes[-1] == "FAILED_INFRASTRUCTURE"
    assert any(point.name == "agent_accepted_head_orphan_total" for point in observation.points)


@pytest.mark.postgres
def test_v2_disconnect_after_confirmed_commit_recovers_one_canonical_mutation(
    vertical_runtime: VerticalRuntime,
    monkeypatch,
) -> None:
    runtime = vertical_runtime
    conversation_id = f"pr7a-disconnect-{uuid4()}"
    _, pending = _start(runtime.facade, runtime.context, runtime.house_id, conversation_id)
    committed = Event()
    release_delivery = Event()
    producer_finished = Event()
    publication_order = []
    lifecycle = runtime.app.state.agent_lifecycle
    original_checkpointer = lifecycle._checkpointer
    lifecycle._checkpointer = _RecordAcceptedPublication(
        original_checkpointer,
        publication_order,
    )

    original_commit = SqlAlchemyRepairUnitOfWork.commit

    def record_business_commit(unit_of_work) -> None:
        original_commit(unit_of_work)
        publication_order.append("business_commit")

    monkeypatch.setattr(SqlAlchemyRepairUnitOfWork, "commit", record_business_commit)

    def source():
        try:
            for event in runtime.facade.stream_resume(
                conversation_id=conversation_id,
                context=runtime.context,
                confirmed=True,
                action_hash=pending["pending_confirmation"]["action_hash"],
            ):
                if event.kind is StreamEventKind.FINAL:
                    with runtime.sessions() as session:
                        assert len(session.execute(select(WorkOrderModel)).scalars().all()) == 1
                    accepted = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(
                        conversation_id
                    )
                    assert accepted is not None and accepted.runtime_cursor is not None
                    publication_order.append("final_event")
                    committed.set()
                    release_delivery.wait(timeout=3)
                yield event
        finally:
            producer_finished.set()

    registry = BoundedStreamExecutionRegistry(1)
    try:
        delivery = BoundedStreamBridge(source, registry=registry).events()
        assert next(delivery).kind is StreamEventKind.TURN_STARTED
        assert committed.wait(timeout=5)
        delivery.close()
        release_delivery.set()
        assert producer_finished.wait(timeout=5)
        assert registry.shutdown(2)
    finally:
        lifecycle._checkpointer = original_checkpointer
        release_delivery.set()
        registry.shutdown(2)
    assert publication_order == [
        "business_commit",
        "accepted_head",
        "final_event",
    ]

    conversation, still_pending = runtime.facade.status(
        conversation_id=conversation_id, context=runtime.context
    )
    accepted = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(conversation_id)
    assert conversation.runtime_version == "v2"
    assert still_pending is None
    assert accepted is not None and accepted.runtime_cursor is not None
    exact = runtime.app.state.langgraph_saver_resource.saver.get_tuple(
        {"configurable": accepted.runtime_cursor.to_dict()}
    )
    assert exact is not None
    with runtime.sessions() as session:
        orders = session.execute(select(WorkOrderModel)).scalars().all()
        assert len(orders) == 1
    with pytest.raises(AgentSessionError) as replay:
        runtime.facade.resume(
            conversation_id=conversation_id,
            context=runtime.context,
            confirmed=True,
            action_hash=pending["pending_confirmation"]["action_hash"],
        )
    assert replay.value.code == AgentSessionErrorCode.NOTHING_PENDING


@pytest.mark.postgres
def test_v2_sync_and_stream_paths_converge_on_canonical_state(
    vertical_runtime: VerticalRuntime,
) -> None:
    runtime = vertical_runtime
    sync_id = f"pr7a-parity-sync-{uuid4()}"
    stream_id = f"pr7a-parity-stream-{uuid4()}"
    sync_pending, sync_data = _start(runtime.facade, runtime.context, runtime.house_id, sync_id)
    stream_events = list(
        runtime.facade.stream_start(
            conversation_id=stream_id,
            context=runtime.context,
            user_text="我要报修厨房水管漏水",
            house_id=runtime.house_id,
            slots={
                "action": "create",
                "category": "WATER_PLUMBING",
                "description": "厨房水管漏水",
                "location": "厨房",
                "urgency": "NORMAL",
            },
        )
    )
    stream_pending = stream_events[-1].turn
    stream_data = turn_data(stream_pending)

    assert sync_pending.conversation.runtime_version == "v2"
    assert stream_pending.conversation.runtime_version == "v2"
    assert sync_data["status"] == stream_data["status"]
    assert sync_data["operation_level"] == stream_data["operation_level"]
    assert sync_data["pending_confirmation"]["tool"] == stream_data["pending_confirmation"]["tool"]
    accepted_sync = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(sync_id)
    accepted_stream = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(stream_id)
    assert accepted_sync.version == accepted_stream.version == 1
    assert accepted_sync.runtime_cursor is not None
    assert accepted_stream.runtime_cursor is not None

    sync_done = runtime.facade.resume(
        conversation_id=sync_id,
        context=runtime.context,
        confirmed=True,
        action_hash=sync_data["pending_confirmation"]["action_hash"],
    )
    streamed_done = list(
        runtime.facade.stream_resume(
            conversation_id=stream_id,
            context=runtime.context,
            confirmed=True,
            action_hash=stream_data["pending_confirmation"]["action_hash"],
        )
    )[-1].turn
    sync_result = turn_data(sync_done)
    stream_result = turn_data(streamed_done)
    assert sync_result["status"] == stream_result["status"]
    assert sync_result["done"] == stream_result["done"] is True
    assert (
        sync_result["facts"]["work_order"]["status"]
        == stream_result["facts"]["work_order"]["status"]
    )
    assert SqlAlchemyCheckpointer(runtime.sessions).version_of(sync_id) == 2
    assert SqlAlchemyCheckpointer(runtime.sessions).version_of(stream_id) == 2
    sync_after = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(sync_id)
    stream_after = SqlAlchemyCheckpointer(runtime.sessions).load_accepted(stream_id)
    assert sync_after.runtime_cursor is not None
    assert stream_after.runtime_cursor is not None
    saver = runtime.app.state.langgraph_saver_resource.saver
    assert saver.get_tuple({"configurable": sync_after.runtime_cursor.to_dict()}) is not None
    assert saver.get_tuple({"configurable": stream_after.runtime_cursor.to_dict()}) is not None
