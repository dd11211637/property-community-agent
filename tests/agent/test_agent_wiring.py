"""装配收尾测试 — PRD §3.3 / §6.5。

验证 ``build_production_container`` 装配后 ``app.state.agent_runner`` 存在，
对话接口不再返回 503 ``ADAPTER_NOT_CONFIGURED``。

这是 §6.5 对外接口在统一应用里真正可用的最后一道验收：不补这一步，
``main.py`` 虽已挂载 ``agent_router``，但运行时未装配，所有对话端点永远 503。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.adapters.api.router import router as agent_router
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.platform.container import build_production_container
from property_agent.platform.context import RequestContext
from property_agent.platform.dependencies import (
    get_request_context as platform_get_request_context,
)
from property_agent.platform.infrastructure import database as db_module
from property_agent.platform.infrastructure.orm_models import Base


@pytest.fixture
def sqlite_platform(monkeypatch):
    """把 platform DB 指向 sqlite 内存库并建全部表。"""
    monkeypatch.setattr(db_module, "DATABASE_URL", "sqlite://")
    monkeypatch.setattr(db_module, "_engine", None)
    monkeypatch.setattr(db_module, "_SessionLocal", None)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionLocal", factory)
    yield factory
    engine.dispose()


def _trusted_context() -> RequestContext:
    house = uuid4()
    return RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=frozenset({"RESIDENT"}),
        request_id="req-wiring",
        current_house_id=house,
        bound_house_ids=frozenset({house}),
    )


def test_production_container_assembles_agent_runner(sqlite_platform):
    """装配后 app.state.agent_runner 是 AgentSessionRunner 实例。"""
    app = FastAPI()
    build_production_container(app)
    assert isinstance(app.state.agent_runner, AgentSessionRunner)


def test_agent_endpoint_no_longer_returns_503(sqlite_platform):
    """装配后对话接口返回 200 而非 503 ADAPTER_NOT_CONFIGURED。"""
    app = FastAPI()
    build_production_container(app)
    app.include_router(agent_router)

    ctx = _trusted_context()
    app.dependency_overrides[platform_get_request_context] = lambda: ctx

    client = TestClient(app)
    resp = client.post(
        "/api/agent/conversations/conv-warm/messages",
        json={"text": "你好"},
    )
    assert resp.status_code != 503
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["done"] is True
    assert body["data"]["pending_confirmation"] is None


def test_agent_endpoint_read_path_works_post_wiring(sqlite_platform):
    """装配后读路径（报修列表）经真实业务 service 返回 200，非 503。"""
    app = FastAPI()
    build_production_container(app)
    app.include_router(agent_router)

    ctx = _trusted_context()
    app.dependency_overrides[platform_get_request_context] = lambda: ctx

    client = TestClient(app)
    resp = client.post(
        "/api/agent/conversations/conv-read/messages",
        json={
            "text": "查一下我的报修记录",
            "house_id": str(ctx.current_house_id),
            "slots": {"query_type": "list"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["intent"] == "REPAIR"
    assert body["data"]["done"] is True
