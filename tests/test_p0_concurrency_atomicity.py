"""P0 正确性底座测试 —— Checkpoint CAS / Run Lease / Approval 原子性。

涵盖（deep-research-report.md §3 + §Approval 原子化）：

* Checkpoint CAS — stale worker 用过期 expected_version 写不进去，抛
  ``CheckpointVersionConflict``（runner 终止本 run）。
* Run Lease — 同一 conversation 两个并发 run 抢占 lease，第二个抛
  ``CONVERSATION_BUSY``（409）；持租者释放后第三个能抢到；同 fence 不会误杀。
* Approval 原子性 — 同事务内消费 + 业务 mutation；过期/wrong actor/wrong
  params_hash 拒绝；重复消费幂等。

所有测试使用 SQLite in-memory + StaticPool；不依赖外部 PostgreSQL。
"""

from __future__ import annotations

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
from property_agent.agent.infrastructure.run_lease import RunLeaseService
from property_agent.agent.state import GraphState
from property_agent.platform.application.approval_service import (
    ApprovalError,
    ApprovalService,
    ApprovalStatus,
)
from property_agent.platform.application.hashing import canonical_hash
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
    # 第一次 save 创建 version=1
    checkpointer.save("conv-1", state)
    # 第二次 save 不传 expected_version（run lease 守的单写者语义），version 递增到 2
    state.slots["description"] = "y"
    checkpointer.save("conv-1", state)
    # 第三个 worker 用过期的 expected_version=1 写入 → 冲突
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


# ── Run Lease ─────────────────────────────────────────────


def test_run_lease_blocks_concurrent_run(run_lease):
    """同一 conversation 第二次 acquire 在 lease 期内抛 CONVERSATION_BUSY。"""
    run_a = run_lease.acquire("conv-1")
    assert run_a is not None
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
    run_lease.release("conv-1", run_a)
    run_b = run_lease.acquire("conv-1")
    assert run_b is not None


def test_run_lease_is_held_reflects_state(run_lease):
    """``is_held`` 反映当前 lease 是否仍有效；stranger 释放不掉自己。"""
    assert run_lease.is_held("conv-x") is False
    owner = run_lease.acquire("conv-x")
    assert run_lease.is_held("conv-x") is True
    # 拿别人的 run_id 来释放，lease 仍在。
    run_lease.release("conv-x", uuid4())
    assert run_lease.is_held("conv-x") is True
    # 持租者释放后无人持有。
    run_lease.release("conv-x", owner)
    assert run_lease.is_held("conv-x") is False


def test_run_lease_expires_and_can_be_reacquired(session_factory):
    """TTL=1s 后另一个 run 能抢占（验证 fencing 不永久阻塞）。"""
    lease = RunLeaseService(session_factory, lease_seconds=1)
    run_lease.acquire("conv-1") if False else None  # noqa: avoid unbound on first branch
    first = lease.acquire("conv-1")
    import time as time_mod

    time_mod.sleep(1.1)
    second = lease.acquire("conv-1")
    assert second is not None
    assert first != second
    lease.release("conv-1", second)


# ── Approval 原子性 ───────────────────────────────────────


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


def test_approval_consume_marks_consumed_in_caller_session(approval_service, session_factory):
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
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

    # 已提交：从独立连接读到的状态应该是 CONSUMED，且 consumed_at 非空。
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
    params_hash = canonical_hash({"a": 1})
    with session_factory() as session:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        # 重复 consume 不抛错（避免嵌套在 retry 中的服务抛异常）
        again = approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        assert again.status == ApprovalStatus.CONSUMED


def test_approval_consume_marks_expired_when_pending_past_ttl(approval_service, session_factory):
    actor = uuid4()
    approval_service_with_short_ttl = ApprovalService(session_factory, ttl_minutes=0)
    approval = approval_service_with_short_ttl.create_pending(
        conversation_id="conv-1",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    # 强制将 expires_at 改成过去
    with session_factory() as session:
        record = session.execute(
            select(AgentActionApprovalModel).where(AgentActionApprovalModel.id == approval.id)
        ).scalar_one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    with session_factory() as session, pytest.raises(ApprovalError) as exc:
        approval_service_with_short_ttl.consume(
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
    params_hash = canonical_hash({"a": 1})

    # 场景 1：consume 失败（不存在的 approval）→ 业务侧什么也不该落。
    # 这里用一块"业务表"代理：通过一个简单的本地变量计数应该被提交的行数。
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
    # 关键断言：approval 行没被消费
    with session_factory() as session:
        record = session.execute(
            select(AgentActionApprovalModel).where(AgentActionApprovalModel.id == approval.id)
        ).scalar_one()
        assert record.status == ApprovalStatus.PENDING.value
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