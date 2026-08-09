"""6.5.f 持久化与恢复测试 — PRD §6.5.8。

覆盖：
* thread_id == 稳定 conversation_id，持久化 Checkpointer 可读回图执行状态
* 应用重启（进程内以"新建全套对象"模拟）后，待确认流程仍可恢复并执行
* Conversation 业务表记录所有权 / 当前房屋 / 接管状态 / 生命周期
* 恢复前三道闸：用户会话、房屋绑定、确认有效期，任一不过都拒绝 resume
* interrupt 之前无任何副作用；幂等键跨重启保持不变（重复确认不会二次落库）
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.application import (
    AgentRecoveryService,
    AgentSessionError,
    AgentSessionErrorCode,
    AgentSessionRunner,
    ConversationService,
    ConversationStatus,
)
from property_agent.agent.graph import build_agent_graph
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.models import (
    AgentCheckpointModel,
    ConversationModel,
)
from property_agent.agent.model_gateway import DeterministicModelGateway
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import idempotency_key
from property_agent.platform.infrastructure.orm_models import Base

AGENT_TABLES = [ConversationModel.__table__, AgentCheckpointModel.__table__]


# ------------------------------ 夹具 ------------------------------


@dataclass(frozen=True)
class Ctx:
    """可信请求上下文（由 API 层解析出来，永不采信用户自述）。"""

    actor_id: UUID
    community_id: UUID
    house_ids: frozenset[UUID]


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def tool(self, name: str, result: dict | None = None):
        def _call(state: GraphState) -> dict:
            self.calls.append((name, dict(state.slots)))
            return result or {"ok": True, "tool": name, "data": {"count": 0}}

        return _call

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


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


@pytest.fixture
def ctx() -> Ctx:
    house = uuid4()
    return Ctx(actor_id=uuid4(), community_id=uuid4(), house_ids=frozenset({house}))


def boot(session_factory, *, clock=None, ttl_seconds=300):
    """搭一套完整运行时。重复调用 = 模拟应用重启（对象全新，数据库不变）。"""
    rec = Recorder()
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
    runner = AgentSessionRunner(
        graph=graph, conversations=conversations, recovery=recovery
    )
    return runner, rec, checkpointer, conversations, recovery


REPAIR_SLOTS = {
    "action": "create",
    "category": "WATER_PLUMBING",
    "location": "厨房",
    "description": "水管漏水",
}


def start_repair(runner, ctx: Ctx, conversation_id: str = "conv-1"):
    return runner.start(
        conversation_id=conversation_id,
        context=ctx,
        user_text="我要报修",
        house_id=next(iter(ctx.house_ids)),
        slots=dict(REPAIR_SLOTS),
    )


# ------------------------------ Checkpointer ------------------------------


def test_checkpointer_roundtrip_uses_conversation_id_as_thread_id(session_factory, ctx):
    cp = SqlAlchemyCheckpointer(session_factory)
    state = GraphState(
        conversation_id="conv-x",
        actor_id=ctx.actor_id,
        community_id=ctx.community_id,
        current_house_id=next(iter(ctx.house_ids)),
        intent="REPAIR",
        slots={"category": "WATER_PLUMBING", "work_order_id": uuid4()},
    )

    cp.save("conv-x", state)
    cp.save("conv-x", state)
    loaded = cp.load("conv-x")

    assert cp.list_threads() == ["conv-x"]
    assert cp.version_of("conv-x") == 2  # 同一线程只保留最新版并递增
    assert loaded is not None
    assert loaded.conversation_id == "conv-x"
    assert loaded.actor_id == ctx.actor_id
    assert loaded.current_house_id == next(iter(ctx.house_ids))
    assert cp.load("missing") is None


def test_checkpointer_marks_pending_threads(session_factory, ctx):
    runner, rec, cp, _, _ = boot(session_factory)
    start_repair(runner, ctx)

    assert cp.pending_threads() == ["conv-1"]
    assert rec.calls == []  # 中断发生在任何写调用之前


# ------------------------------ 重启恢复 ------------------------------


def test_pending_confirmation_survives_restart(session_factory, ctx):
    runner, rec, _, conversations, _ = boot(session_factory)
    turn = start_repair(runner, ctx)

    assert turn.awaiting_confirmation is True
    assert turn.interrupt["action"]["tool"] == "repair_create"
    assert rec.calls == []
    assert turn.conversation.status == ConversationStatus.WAITING_CONFIRM.value

    # —— 应用重启：全新的图、Checkpointer、服务对象，只有数据库保留下来 ——
    runner2, rec2, _, conversations2, _ = boot(session_factory)
    resumed = runner2.resume(
        conversation_id="conv-1", context=ctx, confirmed=True, confirmation_token="tok-9"
    )

    assert resumed.done is True
    assert rec2.names == ["repair_create"]  # 重启后才真正落库
    assert resumed.state.confirmation_token == "tok-9"
    assert resumed.state.tool_result["data"]["work_order"]["id"] == "W-1"
    assert conversations2.get("conv-1").status == ConversationStatus.ACTIVE.value
    assert conversations.get("conv-1").handover_required is False


def test_cancel_after_restart_creates_nothing(session_factory, ctx):
    runner, _, _, _, _ = boot(session_factory)
    start_repair(runner, ctx)

    runner2, rec2, cp2, _, _ = boot(session_factory)
    resumed = runner2.resume(conversation_id="conv-1", context=ctx, confirmed=False)

    assert resumed.done is True
    assert rec2.calls == []
    assert resumed.state.tool_result is None
    assert cp2.pending_threads() == []


def test_idempotency_key_is_stable_across_restart(session_factory, ctx):
    """interrupt 前无副作用；恢复后幂等键不变 ⇒ 重复确认不会产生第二个业务对象。"""
    runner, rec, cp, _, _ = boot(session_factory)
    work_order_id = uuid4()
    runner.start(
        conversation_id="conv-1",
        context=ctx,
        user_text="我要报修",
        house_id=next(iter(ctx.house_ids)),
        slots={**REPAIR_SLOTS, "work_order_id": work_order_id},
    )
    before = cp.load("conv-1")
    key_before = idempotency_key(before, "repair_create", before.pending_action["params"])

    # 重启后从数据库读回（UUID 已被序列化成字符串）
    _, _, cp2, _, _ = boot(session_factory)
    after = cp2.load("conv-1")
    key_after = idempotency_key(after, "repair_create", after.pending_action["params"])

    assert after.slots["work_order_id"] == str(work_order_id)
    assert key_before == key_after
    assert len(key_after) <= 128
    assert rec.calls == []


# ------------------------------ Conversation 业务表 ------------------------------


def test_conversation_records_ownership_house_and_lifecycle(session_factory, ctx):
    runner, _, _, conversations, _ = boot(session_factory)
    house_id = next(iter(ctx.house_ids))
    start_repair(runner, ctx)

    snapshot = conversations.get("conv-1")
    assert snapshot.actor_id == ctx.actor_id
    assert snapshot.community_id == ctx.community_id
    assert snapshot.current_house_id == house_id
    assert snapshot.last_intent == "REPAIR"
    assert snapshot.status == ConversationStatus.WAITING_CONFIRM.value

    conversations.close("conv-1")
    assert conversations.get("conv-1").is_closed is True


def test_high_risk_turn_marks_conversation_handover(session_factory, ctx):
    runner, rec, _, conversations, _ = boot(session_factory)
    turn = runner.start(
        conversation_id="conv-h",
        context=ctx,
        user_text="发一条停水公告",
        slots={"action": "publish", "announcement_id": "A-1"},
    )

    assert rec.calls == []
    assert turn.state.handover_required is True
    assert conversations.get("conv-h").status == ConversationStatus.HANDOVER.value
    assert conversations.get("conv-h").handover_required is True


def test_start_is_idempotent_but_rejects_foreign_actor(session_factory, ctx):
    _, _, _, conversations, _ = boot(session_factory)
    first = conversations.start(conversation_id="conv-1", context=ctx)
    again = conversations.start(conversation_id="conv-1", context=ctx)
    assert first.conversation_id == again.conversation_id

    intruder = Ctx(actor_id=uuid4(), community_id=ctx.community_id, house_ids=frozenset())
    with pytest.raises(AgentSessionError) as excinfo:
        conversations.start(conversation_id="conv-1", context=intruder)
    assert excinfo.value.code == AgentSessionErrorCode.SESSION_MISMATCH.value


# ------------------------------ 恢复前三道闸 ------------------------------


def test_resume_rejects_other_user_session(session_factory, ctx):
    runner, _, _, _, _ = boot(session_factory)
    start_repair(runner, ctx)

    runner2, rec2, _, _, _ = boot(session_factory)
    intruder = Ctx(
        actor_id=uuid4(), community_id=ctx.community_id, house_ids=ctx.house_ids
    )
    with pytest.raises(AgentSessionError) as excinfo:
        runner2.resume(conversation_id="conv-1", context=intruder, confirmed=True)

    assert excinfo.value.code == AgentSessionErrorCode.SESSION_MISMATCH.value
    assert rec2.calls == []


def test_resume_rejects_closed_conversation(session_factory, ctx):
    runner, _, _, conversations, _ = boot(session_factory)
    start_repair(runner, ctx)
    conversations.close("conv-1")

    runner2, rec2, _, _, _ = boot(session_factory)
    with pytest.raises(AgentSessionError) as excinfo:
        runner2.resume(conversation_id="conv-1", context=ctx, confirmed=True)

    assert excinfo.value.code == AgentSessionErrorCode.CONVERSATION_CLOSED.value
    assert rec2.calls == []


def test_resume_rejects_revoked_house_binding(session_factory, ctx):
    runner, _, _, _, _ = boot(session_factory)
    start_repair(runner, ctx)

    # 绑定被撤销：可信上下文里不再包含这套房
    unbound = Ctx(
        actor_id=ctx.actor_id, community_id=ctx.community_id, house_ids=frozenset()
    )
    runner2, rec2, cp2, _, _ = boot(session_factory)
    with pytest.raises(AgentSessionError) as excinfo:
        runner2.resume(conversation_id="conv-1", context=unbound, confirmed=True)

    assert excinfo.value.code == AgentSessionErrorCode.HOUSE_BINDING_REVOKED.value
    assert rec2.calls == []
    assert cp2.pending_threads() == []  # 待确认已作废，不会被"接着执行"


def test_resume_rejects_expired_confirmation(session_factory, ctx):
    runner, _, _, _, _ = boot(session_factory)
    start_repair(runner, ctx)

    late = lambda: datetime.now(timezone.utc) + timedelta(minutes=10)  # noqa: E731
    runner2, rec2, cp2, conversations2, _ = boot(session_factory, clock=late)
    with pytest.raises(AgentSessionError) as excinfo:
        runner2.resume(conversation_id="conv-1", context=ctx, confirmed=True)

    assert excinfo.value.code == AgentSessionErrorCode.CONFIRMATION_EXPIRED.value
    assert rec2.calls == []
    assert cp2.pending_threads() == []
    assert conversations2.get("conv-1").status == ConversationStatus.ACTIVE.value
    assert cp2.load("conv-1").confirmation_token is None


def test_resume_without_pending_action_is_rejected(session_factory, ctx):
    runner, _, _, _, _ = boot(session_factory)
    runner.start(
        conversation_id="conv-r",
        context=ctx,
        user_text="查一下我的账单",
        house_id=next(iter(ctx.house_ids)),
        slots={"query_type": "list"},
    )

    runner2, rec2, _, _, _ = boot(session_factory)
    with pytest.raises(AgentSessionError) as excinfo:
        runner2.resume(conversation_id="conv-r", context=ctx, confirmed=True)

    assert excinfo.value.code == AgentSessionErrorCode.NOTHING_PENDING.value
    assert rec2.calls == []


def test_resume_without_conversation_is_rejected(session_factory, ctx):
    _, _, _, _, recovery = boot(session_factory)
    with pytest.raises(AgentSessionError) as excinfo:
        recovery.restore("never-existed", ctx)
    assert excinfo.value.code == AgentSessionErrorCode.CONVERSATION_NOT_FOUND.value


def test_restore_reinjects_trusted_identity(session_factory, ctx):
    """快照里的身份不可信：恢复时一律用可信上下文覆盖。"""
    runner, _, cp, _, recovery = boot(session_factory)
    start_repair(runner, ctx)

    tampered = cp.load("conv-1")
    tampered.actor_id = uuid4()
    tampered.community_id = uuid4()
    cp.save("conv-1", tampered)

    restored = recovery.restore("conv-1", ctx)

    assert restored.state.actor_id == ctx.actor_id
    assert restored.state.community_id == ctx.community_id
    assert restored.pending_action["tool"] == "repair_create"
