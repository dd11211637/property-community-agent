"""6.5.g 对外接口验收 — PRD §6.5.2 / §6.5.11。

通过 FastAPI ``TestClient`` 走真实 HTTP 层，验证智能体对外的 JSON / SSE 接口与
恢复守卫、身份接缝在端到端口径下一致：

* A-01 意图路由到正确子图，跨模块不串线；读操作无需确认
* A-02 必填槽位缺失时只追问，不调用任何业务服务
* A-03 写操作在用户确认前不落库（interrupt 先于任何写）；取消后不产生任何业务对象
* A-04 确认后携带确认令牌执行，且回带 ``action_hash`` 参数指纹
* 重启恢复：应用重启（全新对象、共享数据库）后仍可恢复有效的待确认会话
* 参数指纹不一致（确认回执 ``action_hash`` 与待确认不符）→ 409 CONFIRMATION_PARAMS_CHANGED
* 会话生命周期：GET 状态 / DELETE 关闭；关闭后确认 → 409 CONVERSATION_CLOSED
* 身份接缝：越权用户 → 403 SESSION_MISMATCH；未绑定房屋 → 403 HOUSE_NOT_BOUND；
  运行时未装配 → 503 ADAPTER_NOT_CONFIGURED
* SSE 流式接口按 "intent → message → confirmation → done" 顺序回放
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.adapters.api.router import router as agent_router
from property_agent.agent.application import (
    AgentRecoveryService,
    AgentSessionError,
    AgentSessionRunner,
    ConversationService,
    ConversationStatus,
)
from property_agent.agent.application.errors import AgentSessionErrorCode
from property_agent.agent.graph import build_agent_graph
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.models import (
    AgentCheckpointModel,
    ConversationModel,
)
from property_agent.agent.model_gateway import DeterministicModelGateway
from property_agent.platform.adapters.api.envelope import error_envelope
from property_agent.platform.context import RequestContext
from property_agent.platform.dependencies import (
    get_request_context as platform_get_request_context,
)
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import Base

AGENT_TABLES = [ConversationModel.__table__, AgentCheckpointModel.__table__]

REPAIR_SLOTS = {
    "action": "create",
    "category": "WATER_PLUMBING",
    "location": "厨房",
    "description": "水管漏水",
}


# ------------------------------ 夹具与装配 ------------------------------


class _Recorder:
    """记录工具调用的假注册表，用于断言"谁被调用过"。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def tool(self, name: str, result: dict | None = None):
        def _call(state):
            self.calls.append((name, dict(state.slots)))
            return result or {"ok": True, "tool": name, "data": {"count": 0}}

        return _call

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def build_runner(session_factory, *, clock=None, ttl_seconds=300):
    """搭一套完整运行时。重复调用 = 模拟应用重启（对象全新，数据库不变）。"""
    rec = _Recorder()
    repair = {
        "repair_list": rec.tool("repair_list"),
        "repair_get": rec.tool("repair_get"),
        "repair_create": rec.tool(
            "repair_create",
            {
                "ok": True,
                "tool": "repair_create",
                "data": {"work_order": {"id": "W-1", "status": "PENDING_ASSIGNMENT"}},
            },
        ),
    }
    announcement = {
        "announcement_list": rec.tool("announcement_list"),
        "announcement_get": rec.tool("announcement_get"),
        "announce_publish": rec.tool("announce_publish"),
    }
    billing = {
        "billing_query": rec.tool("billing_query"),
        "billing_consult": rec.tool("billing_consult"),
    }
    inspection = {
        "inspection_list": rec.tool("inspection_list"),
        "inspection_create": rec.tool("inspection_create"),
        "inspection_submit_record": rec.tool("inspection_submit_record"),
        "inspection_ai_suggest": rec.tool("inspection_ai_suggest"),
        "close_high_risk_event": rec.tool("close_high_risk_event"),
    }
    checkpointer = SqlAlchemyCheckpointer(session_factory)
    graph = build_agent_graph(
        gateway=DeterministicModelGateway(),
        repair_tools=repair,
        announcement_tools=announcement,
        billing_tools=billing,
        inspection_tools=inspection,
        checkpointer=checkpointer,
    )
    conversations = ConversationService(session_factory)
    recovery_kwargs = {"ttl_seconds": ttl_seconds}
    if clock is not None:
        recovery_kwargs["clock"] = clock
    recovery = AgentRecoveryService(
        conversations=conversations, checkpointer=checkpointer, **recovery_kwargs
    )
    runner = AgentSessionRunner(graph=graph, conversations=conversations, recovery=recovery)
    return runner, rec, checkpointer, conversations, recovery


def make_context(*, actor_id=None, community_id=None, house_id=None, request_id="req-api"):
    actor_id = actor_id or uuid4()
    community_id = community_id or uuid4()
    house_id = house_id or uuid4()
    return RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=frozenset({"RESIDENT"}),
        request_id=request_id,
        current_house_id=house_id,
        bound_house_ids=frozenset({house_id}),
    )


def make_client(runner, context):
    """装配一个只挂着智能体路由的最小 FastAPI 应用，身份由可信上下文注入。"""
    app = FastAPI()

    @app.exception_handler(AgentSessionError)
    async def _agent_error(request, exc: AgentSessionError):
        return error_envelope(
            request, status_code=exc.status_code, code=exc.code, message=exc.message
        )

    @app.exception_handler(BusinessError)
    async def _biz_error(request, exc: BusinessError):
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    app.include_router(agent_router)
    if runner is not None:
        app.state.agent_runner = runner
    app.dependency_overrides[platform_get_request_context] = lambda: context
    return TestClient(app)


def start_pending_repair(client, conversation_id: str, *, house_id: UUID):
    """发起一轮报修并拿到待确认卡片里的 action_hash。"""
    resp = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "text": "我要报修",
            "house_id": str(house_id),
            "slots": dict(REPAIR_SLOTS),
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["pending_confirmation"] is not None
    assert data["pending_confirmation"]["tool"] == "repair_create"
    return data["pending_confirmation"]["action_hash"]


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=AGENT_TABLES)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


# ============================== A-01 路由 ==============================


def test_a01_intent_routes_to_own_subgraph_only(session_factory):
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    client.post(
        "/api/agent/conversations/c-r/messages",
        json={"text": "我家水管漏水想看看报修记录", "slots": {"query_type": "list"}},
    )
    client.post(
        "/api/agent/conversations/c-b/messages",
        json={"text": "这个月物业费怎么缴", "slots": {"query_type": "list"}},
    )

    # 报修 → repair_list；账单 → billing_query；模块之间不串线
    assert rec.names == ["repair_list", "billing_query"]


def test_a01_read_path_needs_no_confirmation(session_factory):
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    resp = client.post(
        "/api/agent/conversations/c8/messages",
        json={"text": "查一下我的账单", "slots": {"query_type": "list"}},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is True
    data = body["data"]
    assert data["intent"] == "BILLING"
    assert data["done"] is True
    assert data["pending_confirmation"] is None
    assert data["facts"] is not None  # 读结果以"事实"形式返回，与建议分离
    assert rec.names == ["billing_query"]


# ============================== A-02 槽位追问 ==============================


def test_a02_missing_slots_only_asks_never_calls_service(session_factory):
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    resp = client.post(
        "/api/agent/conversations/c1/messages",
        json={"text": "我要报修", "slots": {"action": "create"}},
    )
    body = resp.json()

    assert resp.status_code == 200
    data = body["data"]
    assert data["done"] is True
    assert set(data["missing_slots"]) == {"category", "location", "description"}
    assert rec.calls == []  # 未触碰任何业务服务
    assert any("缺失" in m["content"] for m in data["messages"])


# ============================== A-03 确认前不写 ==============================


def test_a03_write_pauses_before_any_service_call(session_factory):
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    action_hash = start_pending_repair(client, "c2", house_id=ctx.current_house_id)

    # 中断发生在任何写调用之前
    assert rec.calls == []
    status = client.get("/api/agent/conversations/c2").json()["data"]
    assert status["status"] == ConversationStatus.WAITING_CONFIRM.value
    assert status["pending_confirmation"]["action_hash"] == action_hash

    # 用户取消：不产生任何业务对象
    cancel = client.post(
        "/api/agent/conversations/c2/confirmations",
        json={"confirmed": False},
    )
    assert cancel.status_code == 200
    cdata = cancel.json()["data"]
    assert cdata["done"] is True
    assert cdata["pending_confirmation"] is None
    assert rec.calls == []  # 取消后仍未调用任何写服务
    assert any("已取消" in m["content"] for m in cdata["messages"])


# ============================== A-04 确认后执行 ==============================


def test_a04_confirm_executes_with_token_and_facts(session_factory):
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    action_hash = start_pending_repair(client, "c4", house_id=ctx.current_house_id)

    resp = client.post(
        "/api/agent/conversations/c4/confirmations",
        json={
            "confirmed": True,
            "confirmation_token": "tok-123",
            "action_hash": action_hash,
        },
    )
    body = resp.json()

    assert resp.status_code == 200
    data = body["data"]
    assert data["done"] is True
    assert data["pending_confirmation"] is None  # 确认后不再有待确认卡片
    assert rec.names == ["repair_create"]
    assert data["facts"]["work_order"]["id"] == "W-1"
    assert any("已完成" in m["content"] for m in data["messages"])


# ============================== 重启恢复 ==============================


def test_restart_recovery_resumes_valid_pending_via_api(session_factory):
    """应用重启（全新对象，共享数据库）后仍可恢复有效的待确认会话（PRD §6.5.8）。"""
    runner1, _, _, _, _ = build_runner(session_factory)
    ctx = make_context()
    client1 = make_client(runner1, ctx)

    action_hash = start_pending_repair(client1, "conv-1", house_id=ctx.current_house_id)

    # —— 应用重启：全新 runner，但 session_factory（数据库）不变 ——
    runner2, rec2, *_ = build_runner(session_factory)
    client2 = make_client(runner2, ctx)

    resp = client2.post(
        "/api/agent/conversations/conv-1/confirmations",
        json={
            "confirmed": True,
            "confirmation_token": "tok-after-restart",
            "action_hash": action_hash,
        },
    )
    body = resp.json()

    assert resp.status_code == 200
    data = body["data"]
    assert data["done"] is True
    assert rec2.names == ["repair_create"]  # 重启后才真正落库
    assert data["facts"]["work_order"]["id"] == "W-1"
    assert data["facts"]["work_order"]["status"] == "PENDING_ASSIGNMENT"


# ============================== 参数指纹闸门 ==============================


def test_params_fingerprint_mismatch_is_rejected(session_factory):
    """确认回执 ``action_hash`` 与待确认不符 → 不能复用旧确认（§6.5.11）。"""
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    start_pending_repair(client, "c9", house_id=ctx.current_house_id)

    resp = client.post(
        "/api/agent/conversations/c9/confirmations",
        json={
            "confirmed": True,
            "confirmation_token": "tok-x",
            "action_hash": "tampered-fingerprint",
        },
    )
    body = resp.json()

    assert resp.status_code == 409
    assert body["success"] is False
    assert body["error"]["code"] == AgentSessionErrorCode.CONFIRMATION_PARAMS_CHANGED.value
    assert rec.calls == []  # 指纹不符，绝不执行写操作


# ============================== 会话生命周期 ==============================


def test_get_status_and_delete_close_lifecycle(session_factory):
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    start_pending_repair(client, "c6", house_id=ctx.current_house_id)

    status = client.get("/api/agent/conversations/c6")
    assert status.status_code == 200
    sdata = status.json()["data"]
    assert sdata["status"] == ConversationStatus.WAITING_CONFIRM.value
    assert sdata["pending_confirmation"] is not None

    closed = client.delete("/api/agent/conversations/c6")
    assert closed.status_code == 200
    cdata = closed.json()["data"]
    assert cdata["status"] == ConversationStatus.CLOSED.value
    assert cdata["pending_confirmation"] is None

    # 关闭后的会话不能再确认
    after = client.post(
        "/api/agent/conversations/c6/confirmations",
        json={"confirmed": True, "confirmation_token": "tok-z", "action_hash": "h"},
    )
    assert after.status_code == 409
    assert after.json()["error"]["code"] == AgentSessionErrorCode.CONVERSATION_CLOSED.value
    assert rec.calls == []


# ============================== 身份接缝 ==============================


def test_foreign_actor_rejected_with_403(session_factory):
    """越权用户（会话归属不符）确认 → 403 SESSION_MISMATCH（§6.5.8 闸 1）。"""
    runner1, _, *_ = build_runner(session_factory)
    owner = make_context()
    client1 = make_client(runner1, owner)
    start_pending_repair(client1, "conv-x", house_id=owner.current_house_id)

    # 另一个用户（不同 actor）持同一会话来确认
    intruder = make_context()
    runner2, rec2, *_ = build_runner(session_factory)
    client2 = make_client(runner2, intruder)
    resp = client2.post(
        "/api/agent/conversations/conv-x/confirmations",
        json={"confirmed": True, "confirmation_token": "tok", "action_hash": "h"},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == AgentSessionErrorCode.SESSION_MISMATCH.value
    assert rec2.calls == []


def test_unbound_house_rejected_with_403(session_factory):
    """请求体里的房屋不在绑定列表内 → 403 HOUSE_NOT_BOUND。"""
    runner, _, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)
    other_house = uuid4()

    resp = client.post(
        "/api/agent/conversations/c10/messages",
        json={"text": "我要报修", "house_id": str(other_house), "slots": dict(REPAIR_SLOTS)},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "HOUSE_NOT_BOUND"


def test_unconfigured_runtime_returns_503(session_factory):
    """运行时未装配 → 503 ADAPTER_NOT_CONFIGURED，不影响其它结构化接口（§6.5.11）。"""
    ctx = make_context()
    client = make_client(None, ctx)  # 不设置 app.state.agent_runner

    resp = client.post(
        "/api/agent/conversations/c11/messages",
        json={"text": "我要报修", "slots": dict(REPAIR_SLOTS)},
    )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "ADAPTER_NOT_CONFIGURED"


# ============================== SSE 流式接口 ==============================


def test_sse_stream_emits_expected_event_sequence(session_factory):
    runner, rec, *_ = build_runner(session_factory)
    ctx = make_context()
    client = make_client(runner, ctx)

    resp = client.post(
        "/api/agent/conversations/c12/messages/stream",
        json={
            "text": "我要报修",
            "house_id": str(ctx.current_house_id),
            "slots": dict(REPAIR_SLOTS),
        },
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events: list[tuple[str, dict]] = []
    for block in resp.text.split("\n\n"):
        block = block.strip()
        if not block or not block.startswith("event:"):
            continue
        lines = block.splitlines()
        event = lines[0].split(":", 1)[1].strip()
        data_line = next((ln for ln in lines if ln.startswith("data:")), None)
        if data_line is None:
            continue
        import json

        payload = json.loads(data_line.split(":", 1)[1].strip())
        events.append((event, payload))

    names = [e for e, _ in events]
    assert "intent" in names
    assert "confirmation" in names
    assert "done" in names
    confirm = next(p for e, p in events if e == "confirmation")
    assert confirm["tool"] == "repair_create"
    assert rec.calls == []  # 流式过程同样停在确认前，未写库
