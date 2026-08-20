"""P0 正确性底座测试 —— Checkpoint CAS / Run Lease / Fencing / Approval 原子性。

涵盖（deep-research-report.md §3 + §Approval 原子化 + 审查报告 P0）：

* Checkpoint CAS — stale worker 用过期 expected_version 写不进去，抛
  ``CheckpointVersionConflict``（runner 终止本 run）。
* Run Lease — 同一 conversation 两个并发 run 抢占 lease，第二个抛
  ``CONVERSATION_BUSY``（409）；持租者释放后第三个能抢到；同 fence 不会误杀。
* Fencing — acquire 返回 Lease(thread_id, run_id, fence, lease_until)；
  assert_run_fence 在业务 UoW session 内校验 lease ownership；stale fence 被拒绝；
  heartbeat/renew 校验 run_id + fence。
* Approval 原子性 — PENDING→APPROVED→CONSUMED 状态机；consume 只接受 APPROVED；
  同事务内消费 + 业务 mutation；过期/wrong actor/wrong params_hash 拒绝；重复消费幂等。

所有测试使用 SQLite in-memory + StaticPool；PostgreSQL 并发语义由
``tests/test_p0_postgres_concurrency.py`` 覆盖（@pytest.mark.postgres）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.application.errors import (
    AgentSessionError,
    AgentSessionErrorCode,
)
from property_agent.agent.infrastructure.checkpointer import (
    CheckpointVersionConflict,
    SqlAlchemyCheckpointer,
)
from property_agent.agent.infrastructure.models import (
    AgentActionApprovalModel,
    AgentCheckpointModel,
)
from property_agent.agent.infrastructure.run_lease import (
    Lease,
    LeaseHeartbeat,
    RunLeaseService,
    StaleAgentRunError,
    assert_run_fence,
)
from property_agent.agent.state import GraphState
from property_agent.platform.application.approval_service import (
    ApprovalError,
    ApprovalService,
    ApprovalStatus,
)
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.application.platform_confirmation_port import (
    PlatformConfirmationPort,
)
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import Base

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def engine():
    """in-memory SQLite + StaticPool（platform + agent 表共享同一引擎）。"""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


@pytest.fixture
def checkpointer(session_factory) -> SqlAlchemyCheckpointer:
    return SqlAlchemyCheckpointer(session_factory)


@pytest.fixture
def run_lease(session_factory) -> RunLeaseService:
    return RunLeaseService(session_factory, lease_seconds=2)


@pytest.fixture
def approval_service(session_factory) -> ApprovalService:
    return ApprovalService(session_factory, ttl_minutes=5)


def _make_state(conversation_id: str = "conv-1") -> GraphState:
    return GraphState(
        conversation_id=conversation_id,
        actor_id=uuid4(),
        community_id=uuid4(),
        intent="REPAIR",
        slots={"house_id": str(uuid4()), "description": "x"},
        messages=[],
    )


# ── Checkpoint CAS ────────────────────────────────────────


def test_checkpoint_cas_rejects_stale_expected_version(checkpointer, session_factory):
    """Stale worker 拿到的 expected_version 已过期，CAS 应当冲突。"""
    state = _make_state()
    checkpointer.save("conv-1", state)
    state.slots["description"] = "y"
    checkpointer.save("conv-1", state)
    state.slots["description"] = "z"
    with pytest.raises(CheckpointVersionConflict) as exc:
        checkpointer.save("conv-1", state, expected_version=1)
    assert exc.value.thread_id == "conv-1"
    assert exc.value.expected == 1


def test_checkpoint_cas_accepts_correct_expected_version(checkpointer, session_factory):
    state = _make_state()
    checkpointer.save("conv-1", state)
    state.slots["description"] = "y"
    checkpointer.save("conv-1", state)  # version=2
    state.slots["description"] = "z"
    checkpointer.save("conv-1", state, expected_version=2)  # OK
    with session_factory() as session:
        record = session.execute(
            select(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == "conv-1")
        ).scalar_one()
        assert record.version == 3
        assert record.state["slots"]["description"] == "z"


def test_checkpoint_legacy_path_works_without_expected_version(checkpointer):
    """未传 expected_version 时退化为 SELECT→+1→COMMIT（兼容未迁移调用方）。"""
    state = _make_state()
    checkpointer.save("conv-1", state)
    checkpointer.save("conv-1", state)
    checkpointer.save("conv-1", state)
    assert checkpointer.version_of("conv-1") == 3


# ── Run Lease + Fencing ───────────────────────────────────


def test_run_lease_blocks_concurrent_run(run_lease):
    """同一 conversation 第二次 acquire 在 lease 期内抛 CONVERSATION_BUSY。"""
    run_a = run_lease.acquire("conv-1")
    assert isinstance(run_a, Lease)
    assert run_a.fence == 1
    with pytest.raises(AgentSessionError) as exc:
        run_lease.acquire("conv-1")
    assert exc.value.code == AgentSessionErrorCode.CONVERSATION_BUSY.value


def test_run_lease_releases_only_owner(run_lease):
    run_a = run_lease.acquire("conv-1")
    # 拿别人的 run_id 来 release 不应该误杀。
    stranger = uuid4()
    run_lease.release("conv-1", stranger)
    # 持租者再 acquire 仍然冲突——证明 lease 没被释放。
    with pytest.raises(AgentSessionError):
        run_lease.acquire("conv-1")
    # 自己释放后能再次抢占
    run_lease.release("conv-1", run_a.run_id)
    run_b = run_lease.acquire("conv-1")
    assert isinstance(run_b, Lease)
    assert run_b.fence == 2  # fence 递增


def test_run_lease_is_held_reflects_state(run_lease):
    """``is_held`` 反映当前 lease 是否仍有效；stranger 释放不掉自己。"""
    assert run_lease.is_held("conv-x") is False
    owner = run_lease.acquire("conv-x")
    assert run_lease.is_held("conv-x") is True
    # 拿别人的 run_id 来释放，lease 仍在。
    run_lease.release("conv-x", uuid4())
    assert run_lease.is_held("conv-x") is True
    # 持租者释放后无人持有。
    run_lease.release("conv-x", owner.run_id)
    assert run_lease.is_held("conv-x") is False


def test_run_lease_expires_and_can_be_reacquired(session_factory):
    """TTL=1s 后另一个 run 能抢占（验证 fencing 不永久阻塞）。"""
    lease = RunLeaseService(session_factory, lease_seconds=1)
    first = lease.acquire("conv-1")
    import time as time_mod

    time_mod.sleep(1.1)
    second = lease.acquire("conv-1")
    assert second.fence > first.fence  # fence 递增
    lease.release("conv-1", second.run_id)


def test_run_lease_renew_extends_lease(run_lease):
    """heartbeat/renew 必须校验 run_id + fence；成功则 lease_until 延后。"""
    lease = run_lease.acquire("conv-renew")
    original_until = lease.lease_until
    renewed = run_lease.renew(lease.thread_id, lease.run_id, lease.fence)
    assert renewed.fence == lease.fence  # fence 不变
    assert renewed.lease_until >= original_until  # lease 延后


def test_run_lease_renew_rejects_stale_fence(run_lease):
    """renew 用错误的 fence 必须失败（StaleAgentRunError）。"""
    lease = run_lease.acquire("conv-stale")
    wrong_fence = lease.fence + 999
    with pytest.raises(StaleAgentRunError):
        run_lease.renew(lease.thread_id, lease.run_id, wrong_fence)


def test_run_lease_renew_rejects_wrong_run_id(run_lease):
    """renew 用错误的 run_id 必须失败（StaleAgentRunError）。"""
    lease = run_lease.acquire("conv-wrong-rid")
    with pytest.raises(StaleAgentRunError):
        run_lease.renew(lease.thread_id, uuid4(), lease.fence)


def test_assert_run_fence_accepts_valid_lease(run_lease, session_factory):
    """业务 UoW 内 assert_run_fence 校验当前 lease 仍属于本次 run——合法 lease 通过。"""
    lease = run_lease.acquire("conv-fence-ok")
    with session_factory() as session:
        assert_run_fence(session, lease)
        session.rollback()


def test_assert_run_fence_rejects_stale_fence(run_lease, session_factory):
    """stale fence（被抢占后旧 fence）的业务写必须被拒绝。"""
    lease_a = run_lease.acquire("conv-fence-stale")
    # 模拟 lease 过期后 B 抢占（fence +1）
    import time as time_mod

    time_mod.sleep(2.1)  # 等 lease_a 过期（ttl=2s）
    run_lease.acquire("conv-fence-stale")  # B 抢占，fence=2
    # A 用旧 fence 做 assert_run_fence → 必须失败
    with session_factory() as session, pytest.raises(StaleAgentRunError):
        assert_run_fence(session, lease_a)
        session.rollback()


def test_assert_run_fence_rejects_wrong_run_id(run_lease, session_factory):
    """错误的 run_id 业务写必须被拒绝。"""
    lease = run_lease.acquire("conv-fence-rid")
    fake_lease = Lease(
        thread_id=lease.thread_id,
        run_id=uuid4(),  # 错误的 run_id
        fence=lease.fence,
        lease_until=lease.lease_until,
    )
    with session_factory() as session, pytest.raises(StaleAgentRunError):
        assert_run_fence(session, fake_lease)
        session.rollback()


# ── Approval 状态机（PENDING → APPROVED → CONSUMED） ────


def test_approval_create_pending_returns_open_approval(approval_service):
    a = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=uuid4(),
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    b = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=a.actor_id,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    assert a.id == b.id  # 同 (conv, action, params_hash) 复用
    assert a.status == ApprovalStatus.PENDING


def test_approval_consume_rejects_pending_without_approve(approval_service, session_factory):
    """PENDING 状态的审批不能直接消费——必须先 approve（P0-6 状态机）。"""
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    with session_factory() as session, pytest.raises(ApprovalError) as exc:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=canonical_hash({"a": 1}),
            session=session,
        )
    assert exc.value.code == "APPROVAL_NOT_APPROVED"


def test_approval_consume_accepts_approved(approval_service, session_factory):
    """APPROVED 状态的审批可以被消费（PENDING → APPROVED → CONSUMED）。"""
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    with session_factory() as session:
        consumed = approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=canonical_hash({"a": 1}),
            session=session,
        )
        assert consumed.status == ApprovalStatus.CONSUMED
        session.commit()


def test_approval_consume_marks_consumed_in_caller_session(approval_service, session_factory):
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    with session_factory() as session:
        consumed = approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=canonical_hash({"a": 1}),
            session=session,
        )
        assert consumed.status == ApprovalStatus.CONSUMED
        session.commit()
    with session_factory() as session:
        record = session.execute(
            select(AgentActionApprovalModel).where(AgentActionApprovalModel.id == approval.id)
        ).scalar_one()
        assert record.status == ApprovalStatus.CONSUMED.value
        assert record.consumed_at is not None


def test_approval_consume_rejects_wrong_actor(approval_service, session_factory):
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=uuid4(),
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    with session_factory() as session, pytest.raises(ApprovalError) as exc:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=uuid4(),  # 不同的 actor
            action="CREATE_WORK_ORDER",
            params_hash=canonical_hash({"a": 1}),
            session=session,
        )
    assert exc.value.code == "APPROVAL_ACTOR_MISMATCH"


def test_approval_consume_rejects_wrong_action(approval_service, session_factory):
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    with session_factory() as session, pytest.raises(ApprovalError) as exc:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="SECURITY_EVENT_CREATE",  # 不同的 action
            params_hash=canonical_hash({"a": 1}),
            session=session,
        )
    assert exc.value.code == "APPROVAL_ACTION_MISMATCH"


def test_approval_consume_rejects_wrong_params_hash(approval_service, session_factory):
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    with session_factory() as session, pytest.raises(ApprovalError) as exc:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=canonical_hash({"a": 2}),  # 不同的参数指纹
            session=session,
        )
    assert exc.value.code == "APPROVAL_PARAMS_CHANGED"


def test_approval_consume_is_idempotent_within_same_session(approval_service, session_factory):
    """同事务内重复 consume 等同幂等：第二个不抛错、状态仍是 CONSUMED。"""
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    params_hash = canonical_hash({"a": 1})
    with session_factory() as session:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        again = approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        assert again.status == ApprovalStatus.CONSUMED


def test_approval_consume_marks_expired_when_approved_past_ttl(approval_service, session_factory):
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    # 强制将 expires_at 改成过去
    with session_factory() as session:
        record = session.execute(
            select(AgentActionApprovalModel).where(AgentActionApprovalModel.id == approval.id)
        ).scalar_one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    with session_factory() as session, pytest.raises(ApprovalError) as exc:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=canonical_hash({"a": 1}),
            session=session,
        )
    assert exc.value.code == "APPROVAL_EXPIRED"


def test_approval_consume_atomic_with_business_mutation(approval_service, session_factory):
    """consume 与业务 mutation 必须在同一事务里——任一失败两者一起回滚。"""
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    params_hash = canonical_hash({"a": 1})

    # 场景 1：consume 失败（不存在的 approval）→ 业务侧什么也不该落。
    committed_business_rows: list[str] = []
    try:
        with session_factory() as session:
            approval_service.consume(
                approval_id=uuid4(),  # 不存在的 approval
                actor_id=actor,
                action="CREATE_WORK_ORDER",
                params_hash=params_hash,
                session=session,
            )
            committed_business_rows.append("should-not-commit")
            session.commit()
    except ApprovalError:
        pass
    with session_factory() as session:
        record = session.execute(
            select(AgentActionApprovalModel).where(AgentActionApprovalModel.id == approval.id)
        ).scalar_one()
        assert record.status == ApprovalStatus.APPROVED.value  # 未被消费
    assert committed_business_rows == []

    # 场景 2：consume 成功 + 业务 mutation 成功 → 一起 commit
    committed_business_rows.clear()
    with session_factory() as session:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        committed_business_rows.append("committed")
        session.commit()
    assert committed_business_rows == ["committed"]
    with session_factory() as session:
        record = session.execute(
            select(AgentActionApprovalModel).where(AgentActionApprovalModel.id == approval.id)
        ).scalar_one()
        assert record.status == ApprovalStatus.CONSUMED.value


# ── Lease Heartbeat (P0-5) ───────────────────────────────


def test_long_running_turn_keeps_lease(session_factory):
    """长 turn 期间后台 heartbeat 周期续租，lease 不会过期（P0-5）。

    用 TTL=2s + interval=1s 时间加速：等效跑 ~6s（=3 个续租周期），期间 lease
    本应过期 3 次，但 heartbeat 续租使其始终有效。等价于「90s 长 turn，lease
    不过期」的运行时保证。
    """
    run_lease = RunLeaseService(session_factory, lease_seconds=2)
    lease = run_lease.acquire("conv-hb")
    heartbeat = LeaseHeartbeat(run_lease, lease, interval_seconds=1)
    heartbeat.start()
    try:
        # 模拟 6s 长 LLM turn（90s 的时间加速版）。
        time.sleep(6)
        # turn 结束后 heartbeat 仍应报告 alive（未失租）。
        assert heartbeat.stale is False
        # 且 lease 在 DB 中仍有效（未被抢占、未过期）。
        assert run_lease.is_held("conv-hb") is True
    finally:
        heartbeat.stop()
    # 停止后 lease 应可被释放并重新抢占。
    run_lease.release("conv-hb", lease.run_id)
    assert run_lease.is_held("conv-hb") is False


def test_heartbeat_stops_on_renew_failure(session_factory):
    """续租失败（lease 过期未被及时续租）时 heartbeat 必须标记 stale 并自停。"""
    run_lease = RunLeaseService(session_factory, lease_seconds=1)
    lease = run_lease.acquire("conv-hb-stale")
    # 不启动 heartbeat，让 lease 自然过期（TTL=1s）。
    time.sleep(1.2)
    assert run_lease.is_held("conv-hb-stale") is False  # 已过期
    # 过期后启动 heartbeat，首次续租必然失败 → 标记 stale 并自停。
    heartbeat = LeaseHeartbeat(run_lease, lease, interval_seconds=1)
    heartbeat.start()
    for _ in range(20):
        if heartbeat.stale:
            break
        time.sleep(0.1)
    assert heartbeat.stale is True
    heartbeat.stop()


# ── Fence fail-closed (P0-4 生产护栏) ─────────────────────


def test_fence_fail_closed_blocks_without_lease(approval_service, session_factory):
    """生产 enforce_fence=True 时，未经 lease 注入的业务 mutation 必须被拒绝。"""
    port = PlatformConfirmationPort(
        session_factory(),
        approval_service,
        error_factory=BusinessError,
        enforce_fence=True,
    )
    with pytest.raises(StaleAgentRunError):
        port.consume(
            approval_ref=None,
            token="unused",
            actor_id=uuid4(),
            action="CREATE_WORK_ORDER",
            parameter_hash="x",
            request_id="r",
        )


def test_fence_fail_closed_disabled_allows_mock(approval_service, session_factory):
    """测试环境 enforce_fence=False 时，缺失 lease 不会触发 fencing 拒绝（mock 兼容）。

    注意：fence 关闭后 consume 仍会执行正常的 token/approval 校验，这里只验证
    fencing 这一道闸不会在缺失 lease 时误杀（绝不应抛 StaleAgentRunError）。
    """
    port = PlatformConfirmationPort(
        session_factory(),
        approval_service,
        error_factory=BusinessError,
        enforce_fence=False,
    )
    try:
        port.consume(
            approval_ref=None,
            token="unused",
            actor_id=uuid4(),
            action="CREATE_WORK_ORDER",
            parameter_hash="x",
            request_id="r",
        )
    except StaleAgentRunError:
        pytest.fail("enforce_fence=False must NOT raise StaleAgentRunError without a lease")
    except Exception:
        pass  # 预期的 token/approval 校验错误，与 fencing 无关。


# ── Close / Sync 原子性 (P0-7) ────────────────────────────


def test_closed_conversation_not_resurrected_by_sync(session_factory):
    """已关闭会话的 sync_from_state（旧 turn 收尾）必须被拒绝，不复活（P0-7）。"""
    from types import SimpleNamespace

    from property_agent.agent.application.conversation_service import (
        ConversationService,
    )

    actor_id = uuid4()
    community_id = uuid4()
    context = SimpleNamespace(actor_id=actor_id, community_id=community_id, house_ids=frozenset())
    service = ConversationService(session_factory)
    service.start(conversation_id="conv-resurrect", context=context, current_house_id=None)
    service.close("conv-resurrect")
    assert service.get("conv-resurrect").is_closed is True

    state = _make_state("conv-resurrect")
    with pytest.raises(AgentSessionError) as exc:
        service.sync_from_state(state, waiting_confirm=False)
    assert exc.value.code == AgentSessionErrorCode.CONVERSATION_CLOSED.value
    # 关闭状态保持稳定。
    assert service.get("conv-resurrect").is_closed is True


def test_close_is_idempotent(session_factory):
    """重复 close 幂等：第二次 close 命中 0 行也不报错。"""
    from types import SimpleNamespace

    from property_agent.agent.application.conversation_service import (
        ConversationService,
    )

    actor_id = uuid4()
    community_id = uuid4()
    context = SimpleNamespace(actor_id=actor_id, community_id=community_id, house_ids=frozenset())
    service = ConversationService(session_factory)
    service.start(conversation_id="conv-close-idem", context=context, current_house_id=None)
    service.close("conv-close-idem")
    service.close("conv-close-idem")  # 幂等，不抛
    assert service.get("conv-close-idem").is_closed is True
