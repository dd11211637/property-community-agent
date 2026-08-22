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
from property_agent.agent.model_gateway import DeterministicModelGateway, ModelAnalysis
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


def boot(session_factory, *, clock=None, ttl_seconds=300, gateway=None):
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
        "announcement_draft": rec.tool(
            "announcement_draft",
            {
                "ok": True,
                "tool": "announcement_draft",
                "data": {"draft": {"title": "公告", "body": "正文"}},
            },
        ),
        "announcement_revise": rec.tool(
            "announcement_revise",
            {
                "ok": True,
                "tool": "announcement_revise",
                "data": {"draft": {"title": "公告", "body": "修改后的正文"}},
            },
        ),
        "announcement_create_draft": rec.tool("announcement_create_draft"),
        "announce_publish": rec.tool("announce_publish"),
        "announcement_schedule_publish": rec.tool("announcement_schedule_publish"),
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
        gateway=gateway or DeterministicModelGateway(),
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


def downgrade_checkpoint_to_legacy(session_factory, conversation_id: str) -> None:
    typed_keys = {
        "schema_version",
        "domain",
        "capability_invocation",
        "clarification",
        "proposed_action",
        "orchestration",
    }
    with session_factory() as session:
        record = session.query(AgentCheckpointModel).filter_by(thread_id=conversation_id).one()
        legacy = {key: value for key, value in record.state.items() if key not in typed_keys}
        orchestration = record.state.get("orchestration") or {}
        legacy.update(
            _resume=orchestration.get("resume"),
            _interrupt_node=orchestration.get("interrupt_node"),
            _continuation=orchestration.get("continuation", False),
            _contextual_followup=orchestration.get("contextual_followup", False),
        )
        record.state = legacy
        session.commit()


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


def test_loading_legacy_checkpoint_is_zero_write_and_preserves_pending_resume(session_factory):
    legacy = {
        "conversation_id": "conv-legacy-pending",
        "intent": "BILLING",
        "slots": {"action": "consult", "bill_id": ""},
        "missing_slots": [],
        "pending_action": {
            "tool": "billing_consult",
            "params": {"question": "这笔费用是什么？", "bill_id": None},
        },
        "_interrupt_node": "confirm_write",
    }
    with session_factory() as session:
        session.add(
            AgentCheckpointModel(
                thread_id="conv-legacy-pending",
                version=7,
                state=legacy,
                interrupt_node="confirm_write",
                pending_confirm=True,
            )
        )
        session.commit()

    cp = SqlAlchemyCheckpointer(session_factory)
    loaded = cp.load("conv-legacy-pending")

    assert loaded is not None
    assert loaded.schema_version == 2
    assert loaded.domain.bill_id is None
    assert loaded.proposed_action.capability == "billing_consult"
    assert loaded._interrupt_node == "confirm_write"
    assert cp.version_of("conv-legacy-pending") == 7
    assert cp.pending_threads() == ["conv-legacy-pending"]


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

    # Simulate a pre-PR3 row: decode is in-memory only; the successful resume
    # performs the first normal durable write in the current schema.
    downgrade_checkpoint_to_legacy(session_factory, "conv-1")

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


def test_explicit_inspection_task_switch_clears_unrelated_slots(session_factory, ctx):
    runner, _, cp, _, _ = boot(session_factory)
    runner.start(
        conversation_id="conv-inspection-switch",
        context=ctx,
        user_text="创建巡检任务",
        slots={
            "action": "create",
            "title": "夜间巡检",
            "description": "检查消防通道",
            "point": "1栋大厅",
        },
    )

    turn = runner.start(
        conversation_id="conv-inspection-switch",
        context=ctx,
        user_text="查询巡检任务",
    )

    assert turn.state.slots["action"] == "query"
    assert turn.state.slots["target"] == "task"
    assert "title" not in turn.state.slots
    assert "description" not in turn.state.slots
    assert "point" not in turn.state.slots
    persisted = cp.load("conv-inspection-switch")
    assert persisted._interrupt_node is None
    assert persisted.pending_action["tool"] == "inspection_list"


def test_inspection_location_correction_overwrites_previous_value(session_factory, ctx):
    runner, _, _, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-inspection-correct",
        context=ctx,
        user_text="上报事件",
        slots={
            "action": "report_event",
            "event_type": "EQUIPMENT_FAULT",
            "location": "大厅",
            "description": "照明故障",
            "risk_level": "MEDIUM",
        },
    )
    assert first.awaiting_confirmation

    corrected = runner.start(
        conversation_id="conv-inspection-correct",
        context=ctx,
        user_text="不是大厅，是地下车库",
    )

    assert corrected.state.slots["location"] == "地下车库"
    assert corrected.state.pending_action is None or (
        corrected.state.pending_action["params"]["location"] == "地下车库"
    )


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


def test_announcement_publish_first_loads_reviewable_version(session_factory, ctx):
    runner, rec, _, conversations, _ = boot(session_factory)
    turn = runner.start(
        conversation_id="conv-h",
        context=ctx,
        user_text="发一条停水公告",
        slots={"action": "publish", "announcement_id": "A-1"},
    )

    assert [name for name, _ in rec.calls] == ["announcement_get"]
    assert turn.state.handover_required is False
    assert conversations.get("conv-h").status == ConversationStatus.ACTIVE.value


def test_adopt_announcement_keeps_generated_category_across_turns(session_factory, ctx):
    runner, rec, _, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-adopt",
        context=ctx,
        user_text="帮我写公告",
        slots={
            "action": "draft",
            "topic": "消防设施检查",
            "audience": {},
        },
    )
    # Simulate the production draft tool storing validated generated fields.
    first.state.slots.update(
        title="消防设施检查通知",
        body="请勿遮挡消防通道。",
        category="SAFETY",
        audience={},
        action="create",
    )
    SqlAlchemyCheckpointer(session_factory).save("conv-announcement-adopt", first.state)

    adopted = runner.start(
        conversation_id="conv-announcement-adopt",
        context=ctx,
        user_text="采用这个稿件并保存草稿",
    )

    assert adopted.awaiting_confirmation
    assert adopted.state.missing_slots == []
    assert adopted.state.pending_action["tool"] == "announcement_create_draft"
    assert adopted.state.pending_action["params"]["category"] == "SAFETY"
    assert adopted.state.pending_action["params"]["audience"] == {}
    assert rec.names[-1] != "announcement_create_draft"


def test_adopt_draft_derives_missing_internal_category(session_factory, ctx):
    runner, rec, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-derived-category",
        context=ctx,
        user_text="帮我写公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    # Compatibility case: an older checkpoint contains the visible draft but
    # does not contain the newly system-derived internal category.
    first.state.slots.update(
        title="8月14日停水通知",
        body="因供水设施维修，小区将暂停供水。",
        audience={},
        action="create",
    )
    first.state.slots.pop("category", None)
    checkpointer.save("conv-announcement-derived-category", first.state)

    adopted = runner.start(
        conversation_id="conv-announcement-derived-category",
        context=ctx,
        user_text="采用该稿件",
    )

    assert adopted.awaiting_confirmation
    assert adopted.state.missing_slots == []
    assert adopted.state.pending_action["tool"] == "announcement_create_draft"
    assert adopted.state.pending_action["params"]["category"] == "MAINTENANCE"
    assert rec.names[-1] != "announcement_create_draft"


def test_model_semantic_adoption_reactivates_verified_draft(session_factory, ctx):
    class SemanticAdoptionGateway(DeterministicModelGateway):
        def analyze_with_context(self, text, *, history, trusted_context):
            if text == "就用这版吧":
                assert history
                return ModelAnalysis(
                    intent="ANNOUNCEMENT",
                    confidence=0.98,
                    slots={"action": "create"},
                    provider="semantic-test",
                )
            return super().analyze_with_context(
                text, history=history, trusted_context=trusted_context
            )

    runner, _, checkpointer, _, _ = boot(session_factory, gateway=SemanticAdoptionGateway())
    first = runner.start(
        conversation_id="conv-announcement-semantic-adoption",
        context=ctx,
        user_text="帮我写停水公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    first.state.slots.update(
        title="8月14日停水通知",
        body="因供水设施维修，小区将暂停供水。",
        audience={},
        action="create",
    )
    first.state.slots.pop("category", None)
    checkpointer.save("conv-announcement-semantic-adoption", first.state)

    adopted = runner.start(
        conversation_id="conv-announcement-semantic-adoption",
        context=ctx,
        user_text="就用这版吧",
    )

    assert adopted.awaiting_confirmation
    assert adopted.state.pending_action["tool"] == "announcement_create_draft"
    assert adopted.state.pending_action["params"]["category"] == "MAINTENANCE"


def test_retry_recovers_previous_failed_announcement_operation(session_factory, ctx):
    runner, rec, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-retry",
        context=ctx,
        user_text="帮我写停水公告",
        slots={"action": "draft", "topic": "停水通知", "audience": "{}"},
    )
    first.state.error = "公告受众格式无效"
    first.state.slots.update(action="draft", topic="停水通知", audience="{}")
    checkpointer.save("conv-announcement-retry", first.state)
    calls_before = len(rec.calls)

    retried = runner.start(
        conversation_id="conv-announcement-retry",
        context=ctx,
        user_text="重试",
    )

    assert retried.state.intent == "ANNOUNCEMENT"
    assert retried.state.slots["action"] == "draft"
    assert retried.state.slots["audience"] == "{}"
    assert len(rec.calls) == calls_before + 1
    assert rec.names[-1] == "announcement_draft"


def test_adoption_normalizes_display_audience_before_confirmation(session_factory, ctx):
    runner, _, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-display-audience",
        context=ctx,
        user_text="帮我写停水公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    first.state.slots.update(
        title="8月14日停水通知",
        body="因供水设施维修，小区将暂停供水。",
        category="MAINTENANCE",
        audience="1栋住户",
        action="create",
    )
    checkpointer.save("conv-announcement-display-audience", first.state)

    adopted = runner.start(
        conversation_id="conv-announcement-display-audience",
        context=ctx,
        user_text="采纳",
    )

    assert adopted.awaiting_confirmation
    assert adopted.state.pending_action["params"]["audience"] == {"building_ids": ["1栋"]}


def test_implicit_announcement_revision_keeps_active_draft_context(session_factory, ctx):
    runner, rec, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-revise",
        context=ctx,
        user_text="帮我写公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    first.state.slots.update(
        title="停水通知",
        body="明天暂停供水。",
        category="MAINTENANCE",
        audience={},
        action="create",
    )
    checkpointer.save("conv-announcement-revise", first.state)

    revised = runner.start(
        conversation_id="conv-announcement-revise",
        context=ctx,
        user_text="语气正式一点",
    )

    assert revised.state.intent == "ANNOUNCEMENT"
    assert rec.names[-1] == "announcement_revise"
    called_slots = rec.calls[-1][1]
    assert called_slots["title"] == "停水通知"
    assert called_slots["body"] == "明天暂停供水。"
    assert called_slots["revision_instruction"] == "语气正式一点"


def test_modify_announcement_reason_does_not_fall_into_read_query(session_factory, ctx):
    runner, rec, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-reason",
        context=ctx,
        user_text="帮我写公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    first.state.slots.update(
        title="停水通知",
        body="2026年8月14日暂停供水。",
        category="MAINTENANCE",
        audience={},
        action="create",
        target_date="2026-08-14",
    )
    checkpointer.save("conv-announcement-reason", first.state)

    revised = runner.start(
        conversation_id="conv-announcement-reason",
        context=ctx,
        user_text="修改原因，原因是洪水引发的供水设施损坏，需要检修",
    )

    assert revised.state.intent == "ANNOUNCEMENT"
    assert rec.names[-1] == "announcement_revise"
    assert rec.calls[-1][1]["revision_instruction"].startswith("修改原因")


def test_one_revision_turn_merges_copy_audience_date_and_publish_time(session_factory, ctx):
    runner, rec, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-multi-edit",
        context=ctx,
        user_text="帮我写公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    first.state.trusted_context = {"business_date": "2026-08-13"}
    first.state.slots.update(
        title="停水通知",
        body="2026年8月14日暂停供水。",
        category="MAINTENANCE",
        audience={},
        action="create",
        target_date="2026-08-14",
        scheduled_at="2026-08-13T20:00:00+08:00",
    )
    checkpointer.save("conv-announcement-multi-edit", first.state)

    runner.start(
        conversation_id="conv-announcement-multi-edit",
        context=ctx,
        user_text=("标题简短一点，原因改成管网损坏，受众改为1栋，后天停水，今晚9点发布"),
    )

    slots = rec.calls[-1][1]
    assert rec.names[-1] == "announcement_revise"
    assert slots["audience"] == {"building_ids": ["1栋"]}
    assert slots["target_date"] == "2026-08-15"
    assert slots["scheduled_at"] == "2026-08-13T21:00:00+08:00"
    assert slots["revision_instruction"].startswith("标题简短一点")


def test_revision_invalidates_previous_save_confirmation(session_factory, ctx):
    runner, rec, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-reconfirm",
        context=ctx,
        user_text="帮我写公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    first.state.slots.update(
        title="停水通知",
        body="2026年8月14日暂停供水。",
        category="MAINTENANCE",
        audience={},
        action="create",
    )
    first.state.pending_action = {
        "tool": "announcement_create_draft",
        "params_hash": "old-draft-hash",
    }
    first.state.confirmation_token = "old-token"
    first.state._interrupt_node = "announcement.confirm"
    checkpointer.save("conv-announcement-reconfirm", first.state)

    revised = runner.start(
        conversation_id="conv-announcement-reconfirm",
        context=ctx,
        user_text="标题简短一点，同时补充管网损坏原因",
    )

    assert rec.names[-1] == "announcement_revise"
    assert revised.state.confirmation_token is None
    assert revised.state.pending_action is not first.state.pending_action


def test_announcement_revision_missing_specific_time_asks_for_business_time(session_factory, ctx):
    runner, rec, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-time",
        context=ctx,
        user_text="帮我写公告",
        slots={"action": "draft", "topic": "停水通知", "audience": {}},
    )
    first.state.slots.update(
        title="停水通知",
        body="明天暂停供水。",
        category="MAINTENANCE",
        audience={},
        action="create",
        scheduled_at="2026-08-13T20:00:00+08:00",
    )
    checkpointer.save("conv-announcement-time", first.state)
    calls_before = len(rec.calls)

    turn = runner.start(
        conversation_id="conv-announcement-time",
        context=ctx,
        user_text="明天要有具体时间",
    )

    assert turn.state.intent == "ANNOUNCEMENT"
    assert turn.state.slots["action"] == "revise"
    assert turn.state.missing_slots == ["revision_instruction"]
    assert len(rec.calls) == calls_before
    assert "开始时间和预计结束时间" in turn.state.messages[-1]["content"]
    assert "公告发布时间会继续按原安排保留" in turn.state.messages[-1]["content"]


def test_use_this_draft_cannot_replace_category_with_instruction(session_factory, ctx):
    runner, _, checkpointer, _, _ = boot(session_factory)
    first = runner.start(
        conversation_id="conv-announcement-use",
        context=ctx,
        user_text="帮我写公告",
        slots={"action": "draft", "topic": "消防检查", "audience": {}},
    )
    first.state.slots.update(
        title="消防检查通知",
        body="请勿遮挡消防通道。",
        category="SAFETY",
        audience={},
        action="create",
    )
    checkpointer.save("conv-announcement-use", first.state)

    adopted = runner.start(
        conversation_id="conv-announcement-use",
        context=ctx,
        user_text="使用这个稿件并保存草稿",
    )

    assert adopted.awaiting_confirmation
    params = adopted.state.pending_action["params"]
    assert params["category"] == "SAFETY"
    assert params["audience"] == {}


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
    intruder = Ctx(actor_id=uuid4(), community_id=ctx.community_id, house_ids=ctx.house_ids)
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
    unbound = Ctx(actor_id=ctx.actor_id, community_id=ctx.community_id, house_ids=frozenset())
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
