"""Real PostgreSQL acceptance for the four PR5 specialists and cross-domain graph."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from property_agent.agent.application.composition import close_runtime_resources
from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.orchestration import PlanStatus, SpecialistName
from property_agent.agent.runtime_version import RuntimeSelectionPolicy
from property_agent.announcement.application.commands import ReviewActionCommand
from property_agent.announcement.domain.enums import AnnouncementAction
from property_agent.announcement.infrastructure.models import AnnouncementModel
from property_agent.billing.infrastructure.orm_models import ConsultationModel
from property_agent.inspection.infrastructure.models import (
    InspectionTaskModel,
    SecurityEventModel,
)
from property_agent.platform.container import build_production_container
from property_agent.platform.context import RequestContext
from property_agent.platform.infrastructure.database import dispose_engine
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
class AcceptanceRuntime:
    app: FastAPI
    sessions: Any
    facade: AgentRuntimeFacadeImpl
    context: RequestContext
    house_id: Any


@pytest.fixture
def pr5_runtime() -> AcceptanceRuntime:
    if not POSTGRES_URL:
        pytest.skip("TEST_POSTGRES_URL is required")
    dispose_engine()
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
    yield AcceptanceRuntime(app, sessions, facade, context, house_id)
    close_runtime_resources(app)
    dispose_engine()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_identity(sessions):
    community_id, house_id, actor_id = uuid4(), uuid4(), uuid4()
    with sessions() as session:
        session.add_all(
            [
                CommunityModel(id=community_id, name=f"PR5-{community_id}"),
                HouseModel(
                    id=house_id,
                    community_id=community_id,
                    building="5",
                    unit="1",
                    room_no="501",
                ),
                UserModel(
                    id=actor_id,
                    community_id=community_id,
                    username=f"pr5-{actor_id}",
                    display_name="PR5 Manager",
                    password_hash="not-used-by-agent-test",
                ),
                UserRoleModel(user_id=actor_id, role="MANAGER"),
                UserRoleModel(user_id=actor_id, role="RESIDENT"),
                UserHouseBindingModel(user_id=actor_id, house_id=house_id),
            ]
        )
        session.commit()
    context = RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=frozenset({"MANAGER", "RESIDENT"}),
        request_id="pr5-postgres",
        current_house_id=house_id,
        bound_house_ids=frozenset({house_id}),
    )
    return context, house_id


@pytest.mark.postgres
def test_real_four_domains_cross_domain_hitl_and_human_only(pr5_runtime):
    runtime = pr5_runtime

    cross = _start(runtime, "查一下报修进度，顺便看看物业费。")
    assert cross.done is True
    assert cross.state.plan.status is PlanStatus.COMPLETED
    assert [result.specialist for result in cross.state.specialist_results] == [
        SpecialistName.REPAIR,
        SpecialistName.BILLING,
    ]
    assert [result.capability for result in cross.state.specialist_results] == [
        "repair_list",
        "billing_query",
    ]
    assert "已完成" in cross.reply

    no_issue = _start(
        runtime,
        "看看电梯故障有没有巡检发现，如果真的有问题，准备一份业主公告。",
    )
    assert [result.capability for result in no_issue.state.specialist_results] == [
        "inspection_list"
    ]

    _confirm_write(
        runtime,
        "我要报修厨房漏水",
        {
            "action": "create",
            "description": "厨房漏水",
            "location": "厨房",
            "urgency": "NORMAL",
        },
        "repair_create",
    )
    _confirm_write(
        runtime,
        "提交物业费账单咨询",
        {"action": "consult", "subject": "物业费疑问", "description": "请核对本月费用"},
        "billing_consult",
    )

    draft = _start(runtime, "准备一份停水公告")
    assert draft.done is True
    assert draft.state.specialist_results[-1].capability == "announcement_draft"
    saved = _confirm_write(
        runtime,
        "保存公告草稿",
        {"action": "create", "title": "停水通知", "body": "今晚停水检修", "audience": {}},
        "announcement_create_draft",
    )
    announcement_id = saved.state.tool_result["data"]["data"]["announcement"]["id"]
    submitted = runtime.app.state.announcement_service.submit_review(
        UUID(announcement_id),
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, 1),
        runtime.context,
        idempotency_key=f"submit-{uuid4()}",
    )
    approved = runtime.app.state.announcement_service.review_action(
        UUID(announcement_id),
        ReviewActionCommand(AnnouncementAction.APPROVE, submitted.version),
        runtime.context,
        idempotency_key=f"approve-{uuid4()}",
    )
    _confirm_write(
        runtime,
        "立即发布公告",
        {
            "action": "publish",
            "announcement_id": announcement_id,
            "expected_version": approved.version,
        },
        "announce_publish",
    )

    _confirm_write(
        runtime,
        "创建消防通道巡检任务",
        {
            "action": "create",
            "title": "消防通道巡检",
            "description": "检查消防通道",
            "point": "5栋消防通道",
            "route_points": ["5栋消防通道"],
        },
        "inspection_create",
    )
    event = _confirm_write(
        runtime,
        "上报安防事件",
        {
            "action": "report_event",
            "event_type": "FIRE_HAZARD",
            "risk_level": "LOW_RISK",
            "location": "地下车库",
            "description": "地下车库发现大量烟雾和明火",
        },
        "security_event_create",
    )
    event_data = event.state.tool_result["data"]["data"]["event"]
    assert event_data["risk_level"] == "HIGH_RISK"

    grounded = _start(
        runtime,
        "看看安防事件有没有巡检发现，如果真的有问题，准备一份业主公告。",
    )
    assert [result.capability for result in grounded.state.specialist_results] == [
        "inspection_list",
        "announcement_draft",
    ]

    before = _count(runtime.sessions, SecurityEventModel)
    human_only = _start(
        runtime,
        "关闭高风险安防事件",
        {"action": "close_high_risk", "event_id": event_data["id"]},
    )
    assert human_only.state.handover_required is True
    assert human_only.state.pending_action is None
    assert _count(runtime.sessions, SecurityEventModel) == before

    assert _count(runtime.sessions, WorkOrderModel) == 1
    assert _count(runtime.sessions, ConsultationModel) == 1
    assert _count(runtime.sessions, AnnouncementModel) == 1
    assert _count(runtime.sessions, InspectionTaskModel) == 1
    assert _count(runtime.sessions, SecurityEventModel) == 1


def _start(runtime, text, slots=None):
    return runtime.facade.start(
        conversation_id=f"pr5-pg-{uuid4()}",
        context=runtime.context,
        user_text=text,
        house_id=runtime.house_id,
        slots=slots,
    )


def _confirm_write(runtime, text, slots, capability):
    pending = _start(runtime, text, slots)
    assert pending.done is False
    assert pending.state.pending_action["tool"] == capability
    completed = runtime.facade.resume(
        conversation_id=pending.state.conversation_id,
        context=runtime.context,
        confirmed=True,
        action_hash=pending.state.pending_action["params_hash"],
    )
    assert completed.done is True
    assert completed.state.tool_result.get("ok") is True
    return completed


def _count(sessions, model):
    with sessions() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()
