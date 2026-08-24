"""Production-shaped PR4 v2 Repair vertical acceptance on real PostgreSQL."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from property_agent.agent.adapters.api.presentation import turn_data
from property_agent.agent.application.composition import close_runtime_resources
from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.errors import AgentSessionError, AgentSessionErrorCode
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.application.langgraph_runtime import LangGraphEngine, build_saver_resource
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.models import AgentActionApprovalModel
from property_agent.agent.infrastructure.run_lease import RunLeaseService
from property_agent.agent.runtime_version import RuntimeSelectionPolicy
from property_agent.agent.specialists.repair import RepairPilotSpecialist
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
def vertical_runtime() -> VerticalRuntime:
    if not POSTGRES_URL:
        pytest.skip("TEST_POSTGRES_URL is required")
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    context, house_id = _seed_identity(sessions)
    app = FastAPI()
    build_production_container(app)
    production = app.state.agent_runner
    facade = AgentRuntimeFacadeImpl(
        lifecycle=app.state.agent_lifecycle,
        conversations=ConversationService(sessions),
        policy=RuntimeSelectionPolicy(enabled=True),
        v2_engine=production._v2_engine,
    )
    yield VerticalRuntime(app, sessions, facade, context, house_id)
    close_runtime_resources(app)
    Base.metadata.drop_all(engine)
    engine.dispose()


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
    resource = build_saver_resource(
        dsn=str(POSTGRES_URL).replace("postgresql+psycopg", "postgresql")
    )
    runtime.app.state.langgraph_saver_resource = resource
    engine = LangGraphEngine(
        resource.saver,
        RepairPilotSpecialist(runtime.app.state.agent_capability_executor),
    )
    facade = AgentRuntimeFacadeImpl(
        lifecycle=lifecycle,
        conversations=ConversationService(runtime.sessions),
        policy=RuntimeSelectionPolicy(enabled=True),
        v2_engine=engine,
    )
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
