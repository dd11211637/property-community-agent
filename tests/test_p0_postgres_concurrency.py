"""PostgreSQL 并发集成测试 —— P0 correctness proof（审查报告 P0-8）。

SQLite + StaticPool 无法证明 PostgreSQL 的 row locking / FOR UPDATE / partial
unique index / ON CONFLICT 语义。本套件在真实 PostgreSQL 16 上验证：

* 同 conversation 双请求 → exactly one live writer（另一个 409）
* 首次 conversation 创建竞争 → 不 500，不产生两个 state
* 双 tab confirm → 业务对象最多一个（部分唯一索引 + FOR UPDATE）
* stale fence writer → assert_run_fence 拒绝（StaleAgentRunError）
* stale checkpoint → CAS 不覆盖新 checkpoint
* approval/token/business rollback → 全部一起回滚
* commit 后 retry/idempotency → 返回原 resource
* close + active run → CLOSED 不被旧 run 复活
* 四业务领域 production container write → 无 TypeError

CI: ``python -m pytest -rs`` 且 ``! grep -q "SKIPPED"``（quality.yml）。
本地: ``TEST_POSTGRES_URL=postgresql+psycopg://... python -m pytest -m postgres``。
"""

from __future__ import annotations

import os
import threading
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from property_agent.agent.application.conversation_service import (
    ConversationService,
    ConversationStatus,
)
from property_agent.agent.application.errors import (
    AgentSessionError,
    AgentSessionErrorCode,
)
from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.infrastructure.checkpointer import (
    CheckpointVersionConflict,
    SqlAlchemyCheckpointer,
)
from property_agent.agent.infrastructure.run_lease import (
    RunLeaseService,
    StaleAgentRunError,
    assert_run_fence,
)
from property_agent.agent.runtime_rollout import RolloutConfig, RolloutControl, RuntimeEligibility
from property_agent.agent.runtime_version import RuntimeSelectionPolicy
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import RepairWorkingState
from property_agent.platform.application.approval_service import (
    ApprovalError,
    ApprovalService,
    ApprovalStatus,
)
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import Base

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_URL, reason="requires TEST_POSTGRES_URL and a dedicated PostgreSQL database"
    ),
]


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


@pytest.fixture
def run_lease(session_factory):
    return RunLeaseService(session_factory, lease_seconds=3)


@pytest.fixture
def approval_service(session_factory):
    return ApprovalService(session_factory, ttl_minutes=5)


@pytest.fixture
def checkpointer(session_factory):
    return SqlAlchemyCheckpointer(session_factory)


def _make_state(conversation_id: str = "pg-conv-1") -> GraphState:
    return GraphState(
        conversation_id=conversation_id,
        actor_id=uuid4(),
        community_id=uuid4(),
        intent="REPAIR",
        domain=RepairWorkingState(description="pg test"),
        slots={"house_id": str(uuid4()), "description": "pg test"},
        messages=[],
    )


# ── 1. 同 conversation 双请求 → exactly one live writer ──


def test_concurrent_double_request_one_wins(run_lease):
    """两个线程同时 acquire 同一 conversation，只有一个成功，另一个 409。"""
    results: list[object] = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            lease = run_lease.acquire("pg-double-req")
            results.append(("ok", lease.fence))
            time.sleep(0.2)
            run_lease.release("pg-double-req", lease.run_id)
        except AgentSessionError as exc:
            results.append(("busy", exc.code))

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    ok_results = [r for r in results if r[0] == "ok"]
    busy_results = [r for r in results if r[0] == "busy"]
    assert len(ok_results) == 1, f"exactly one writer should win, got {results}"
    assert len(busy_results) == 1, f"exactly one should get 409, got {results}"


def test_concurrent_new_conversation_persists_one_server_owned_runtime_pin(
    run_lease, session_factory
):
    """The lease/creation boundary persists one assignment under a first-turn race."""
    from property_agent.platform.adapters.api.dependencies import RequestContext

    conversations = ConversationService(session_factory)
    context = RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=frozenset({"RESIDENT"}),
        request_id="pr7c-create-race",
    )
    policy = RuntimeSelectionPolicy(
        control=RolloutControl(
            RolloutConfig(
                basis_points=10_000,
                secret_salt=b"server-owned-rollout-secret-32bytes",
                salt_version="salt-v1",
                config_version="pr7c-test-v1",
            )
        ),
        eligibility=RuntimeEligibility(
            v2_engine_available=True,
            official_saver_available=True,
            model_config_approved=True,
        ),
    )
    barrier = threading.Barrier(2)
    results: list[tuple[str, object]] = []

    def attempt() -> None:
        barrier.wait()
        try:
            lease = run_lease.acquire("pr7c-create-race")
        except AgentSessionError as exc:
            results.append(("busy", exc.code))
            return
        try:
            selected = policy.select_new(
                community_id=context.community_id,
                actor_id=context.actor_id,
                conversation_id="pr7c-create-race",
            )
            snapshot = conversations.start(
                conversation_id="pr7c-create-race",
                context=context,
                runtime_version=selected.value,
            )
            results.append(("ok", snapshot.runtime_version))
            time.sleep(0.2)
        finally:
            run_lease.release("pr7c-create-race", lease.run_id)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(result[0] for result in results) == ["busy", "ok"]
    assert conversations.get("pr7c-create-race").runtime_version == "v2"


# ── 2. 首次 conversation 创建竞争 → 不 500 ────────────────


def test_concurrent_create_pending_does_not_500(approval_service):
    """两个线程同时为同一 (conv, action, params) create_pending，
    部分唯一索引 + FOR UPDATE 保证不产生两条 PENDING，不 500。"""
    actor = uuid4()
    params = {"a": 1}
    results: list[object] = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            approval = approval_service.create_pending(
                conversation_id="pg-create-race",
                actor_id=actor,
                action="CREATE_WORK_ORDER",
                params=params,
            )
            results.append(("ok", approval.id))
        except Exception as exc:
            results.append(("err", type(exc).__name__, str(exc)))

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # 不应该有 500 / IntegrityError；两个都应该返回同一 approval id（复用）。
    assert all(r[0] == "ok" for r in results), f"no error expected, got {results}"
    ids = {r[1] for r in results if r[0] == "ok"}
    assert len(ids) == 1, f"both should reuse same approval, got {ids}"


# ── 3. 双 tab confirm → 业务对象最多一个 ──────────────────


def test_double_consume_produces_one_consumed(approval_service, session_factory):
    """双 tab 同时 confirm：FOR UPDATE + 状态机保证只有一个 CONSUMED，另一个幂等返回。"""
    from property_agent.agent.observability import AgentObservability
    from property_agent.agent.observed_boundaries import ObservedApprovalService

    approval_service = ObservedApprovalService(approval_service, AgentObservability.in_memory())
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="pg-double-confirm",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    params_hash = canonical_hash({"a": 1})

    results: list[object] = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            with session_factory() as session:
                consumed = approval_service.consume(
                    approval_id=approval.id,
                    actor_id=actor,
                    action="CREATE_WORK_ORDER",
                    params_hash=params_hash,
                    session=session,
                )
                session.commit()
                results.append(("ok", consumed.status))
        except ApprovalError as exc:
            results.append(("err", exc.code))

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # 两个都返回 CONSUMED（幂等），但数据库里只有一条 CONSUMED 记录。
    assert all(r[0] == "ok" for r in results), f"both should succeed idempotently, got {results}"
    with session_factory() as session:
        record = session.execute(
            select(
                __import__(
                    "property_agent.agent.infrastructure.models",
                    fromlist=["AgentActionApprovalModel"],
                ).AgentActionApprovalModel
            ).where(
                __import__(
                    "property_agent.agent.infrastructure.models",
                    fromlist=["AgentActionApprovalModel"],
                ).AgentActionApprovalModel.id
                == approval.id
            )
        ).scalar_one()
        assert record.status == ApprovalStatus.CONSUMED.value
        assert record.consumed_at is not None


# ── 4. stale fence writer → assert_run_fence 拒绝 ─────────


def test_stale_fence_writer_rejected(run_lease, session_factory):
    """lease 被 B 抢占后，A 的旧 fence 业务写必须被 assert_run_fence 拒绝。"""
    lease_a = run_lease.acquire("pg-stale-fence")
    # 模拟 lease 过期 + B 抢占
    time.sleep(3.1)  # ttl=3s
    lease_b = run_lease.acquire("pg-stale-fence")
    assert lease_b.fence > lease_a.fence

    # A 用旧 fence 做业务写校验 → StaleAgentRunError
    with session_factory() as session, pytest.raises(StaleAgentRunError):
        assert_run_fence(session, lease_a)
        session.rollback()

    # B 的新 fence 仍然有效
    with session_factory() as session:
        assert_run_fence(session, lease_b)
        session.rollback()


# ── 5. stale checkpoint → CAS 不覆盖新 checkpoint ─────────


def test_stale_checkpoint_does_not_overwrite_new(checkpointer, session_factory):
    """A 拿 expected_version=1，B 先写了 version=2，A 的 CAS 必须冲突。"""
    state = _make_state("pg-cas")
    checkpointer.save("pg-cas", state)  # v1
    # B 写 v2
    state.domain.description = "B wrote this"
    checkpointer.save("pg-cas", state)  # v2
    # A 用过期 expected=1
    state.domain.description = "A stale write"
    with pytest.raises(CheckpointVersionConflict):
        checkpointer.save("pg-cas", state, expected_version=1)

    # v2 内容保留
    with session_factory() as session:
        from property_agent.agent.infrastructure.models import AgentCheckpointModel

        record = session.execute(
            select(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == "pg-cas")
        ).scalar_one()
        assert record.version == 2
        assert record.state["slots"]["description"] == "B wrote this"


# ── 6. approval/token/business rollback ───────────────────


def test_consume_rollback_on_business_failure(approval_service, session_factory):
    """consume 成功但业务 mutation 抛异常 → approval 不保持 CONSUMED。"""
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="pg-rollback",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    params_hash = canonical_hash({"a": 1})

    # consume 成功 + 模拟业务 mutation 失败 → rollback
    with session_factory() as session:
        approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        # 模拟业务 mutation 失败
        session.rollback()

    # approval 不应该保持 CONSUMED（rollback 后仍是 APPROVED）
    with session_factory() as session:
        from property_agent.agent.infrastructure.models import AgentActionApprovalModel

        record = session.execute(
            select(AgentActionApprovalModel).where(AgentActionApprovalModel.id == approval.id)
        ).scalar_one()
        assert record.status == ApprovalStatus.APPROVED.value
        assert record.consumed_at is None


# ── 7. commit 后 retry/idempotency ────────────────────────


def test_retry_after_commit_returns_same_resource(approval_service, session_factory):
    """commit 后 retry consume：幂等返回 CONSUMED，不产生第二个副作用。"""
    actor = uuid4()
    approval = approval_service.create_pending(
        conversation_id="pg-retry",
        actor_id=actor,
        action="CREATE_WORK_ORDER",
        params={"a": 1},
    )
    approval_service.approve(approval_id=approval.id, actor_id=actor)
    params_hash = canonical_hash({"a": 1})

    # 第一次 consume + commit
    with session_factory() as session:
        first = approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        session.commit()
    assert first.status == ApprovalStatus.CONSUMED

    # retry：幂等返回 CONSUMED
    with session_factory() as session:
        second = approval_service.consume(
            approval_id=approval.id,
            actor_id=actor,
            action="CREATE_WORK_ORDER",
            params_hash=params_hash,
            session=session,
        )
        session.commit()
    assert second.status == ApprovalStatus.CONSUMED
    assert second.id == first.id  # 同一 approval


# ── 8. close + active run → CLOSED 不被复活 ───────────────


def test_closed_conversation_not_resurrected_by_old_run(session_factory):
    """CLOSED conversation 不允许被旧 run 的 sync_from_state 恢复为 ACTIVE。"""
    from property_agent.agent.application.conversation_service import (
        ConversationService,
        ConversationStatus,
    )
    from property_agent.agent.state import GraphState

    conversations = ConversationService(session_factory)
    # 创建会话
    from property_agent.platform.adapters.api.dependencies import RequestContext

    actor = uuid4()
    community = uuid4()
    ctx = RequestContext(
        actor_id=actor,
        community_id=community,
        roles=frozenset({"RESIDENT"}),
        request_id="pg-close-race",
    )
    conversations.start(conversation_id="pg-close", context=ctx)
    # 关闭会话
    conversations.close("pg-close")
    # 旧 run 完成后试图 sync_from_state（把状态写成 ACTIVE）
    state = GraphState(
        conversation_id="pg-close",
        actor_id=actor,
        community_id=community,
        intent="REPAIR",
        slots={},
        messages=[],
    )
    with pytest.raises(AgentSessionError) as exc:
        conversations.sync_from_state(state, waiting_confirm=False)
    assert exc.value.code == AgentSessionErrorCode.CONVERSATION_CLOSED.value

    # 确认仍是 CLOSED
    snapshot = conversations.get("pg-close")
    assert snapshot.status == ConversationStatus.CLOSED.value


# ── 9. heartbeat / renew 在 PostgreSQL 上工作 ─────────────


def test_renew_extends_lease_on_postgres(run_lease):
    """renew 在 PostgreSQL 上正确延长 lease_until，校验 run_id + fence。"""
    lease = run_lease.acquire("pg-renew")
    original_until = lease.lease_until
    renewed = run_lease.renew(lease.thread_id, lease.run_id, lease.fence)
    assert renewed.fence == lease.fence
    assert renewed.lease_until >= original_until


def test_renew_rejects_after_preemption(run_lease):
    """lease 被抢占后，旧 run 的 renew 必须失败。"""
    lease_a = run_lease.acquire("pg-renew-stale")
    time.sleep(3.1)
    run_lease.acquire("pg-renew-stale")  # B 抢占
    with pytest.raises(StaleAgentRunError):
        run_lease.renew(lease_a.thread_id, lease_a.run_id, lease_a.fence)


# ── 10. production container wiring（四领域无 TypeError） ──


def test_production_container_wiring_no_typeerror(session_factory):
    """验证 repair/announcement/inspection/billing 四领域的 production container
    build_*_service 函数正确绑定 approval_service，不抛 TypeError。"""
    from property_agent.platform.application.approval_service import ApprovalService
    from property_agent.platform.infrastructure.orm_models import (
        CommunityModel,
    )
    from property_agent.repair.infrastructure.shared_ports import build_shared_ports

    # seed minimal data
    with session_factory() as session:
        session.add(CommunityModel(id=uuid4(), name="pg-wiring-test"))
        session.commit()

    approval_service = ApprovalService(session_factory)

    # repair: build_shared_ports(session, approval_service) 不应抛 TypeError
    with session_factory() as session:
        ports = build_shared_ports(session, approval_service)
        assert ports.confirmations is not None
        assert ports.idempotency is not None

    # announcement
    from property_agent.announcement.infrastructure.shared_ports import build_announcement_ports

    with session_factory() as session:
        ports = build_announcement_ports(session, approval_service)
        assert ports.confirmations is not None

    # inspection
    from property_agent.inspection.infrastructure.shared_ports import build_inspection_ports

    with session_factory() as session:
        ports = build_inspection_ports(session, approval_service)
        assert ports is not None

    # billing
    from property_agent.billing.infrastructure.shared_ports import build_billing_ports

    with session_factory() as session:
        ports = build_billing_ports(session, approval_service)
        assert ports is not None


# ── 11. assert_run_fence 行锁验证 ─────────────────────────


def test_assert_run_fence_holds_row_lock(run_lease, session_factory):
    """assert_run_fence 获取行锁，阻止并发 acquire（直到事务结束）。"""
    lease = run_lease.acquire("pg-fence-lock")
    results: list[object] = []
    competing = threading.Event()

    def acquire_while_locked() -> None:
        competing.set()
        try:
            run_lease.acquire("pg-fence-lock")
            results.append("acquired")
        except AgentSessionError as exc:
            results.append(exc.code)

    # 在一个 session 里 assert_run_fence（获取行锁），不 commit。竞争 acquire
    # 必须放在另一线程；同线程同步调用会等待自己持有的行锁，导致测试自死锁。
    session_a = session_factory()
    try:
        assert_run_fence(session_a, lease)
        contender = threading.Thread(target=acquire_while_locked)
        contender.start()
        assert competing.wait(timeout=2)
        time.sleep(0.2)
        assert contender.is_alive(), "competing acquire should wait for the fence row lock"
        session_a.rollback()
        contender.join(timeout=5)
        assert not contender.is_alive()
        assert results == [AgentSessionErrorCode.CONVERSATION_BUSY]
    finally:
        session_a.close()
    # A 释放后能 acquire
    run_lease.release("pg-fence-lock", lease.run_id)


# ── 12. Memory 真 CAS：双 writer 严格 1 success + 1 conflict ──


def test_memory_double_writer_cas(session_factory):
    """两个线程并发更新同一条 memory：原子 UPDATE...WHERE version=expected
    保证严格一个成功、一个 VERSION_CONFLICT。SQLite 单连接无法证明真正的并发
    丢失更新，必须在 PostgreSQL 上验证（审查报告 P1：Memory 真 CAS）。"""
    from types import SimpleNamespace

    actor_id = uuid4()
    community_id = uuid4()
    house_id = uuid4()
    context = SimpleNamespace(
        actor_id=actor_id, community_id=community_id, house_ids=frozenset({house_id})
    )

    with session_factory() as seed:
        memory = AgentMemoryService(seed).create_memory(
            context, memory_type="PREFERENCE", content="原始偏好", house_id=house_id
        )
    memory_id = UUID(memory["id"])

    results: list[str] = []
    barrier = threading.Barrier(2)

    def worker(label: str) -> None:
        barrier.wait()
        try:
            with session_factory() as session:
                AgentMemoryService(session).update_memory(
                    memory_id, context, content=f"更新-{label}", expected_version=1
                )
            results.append(f"{label}:ok")
        except BusinessError as exc:
            results.append(f"{label}:{exc.code}")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    successes = [result for result in results if result.endswith(":ok")]
    conflicts = [result for result in results if result.endswith(":VERSION_CONFLICT")]
    assert len(successes) == 1, f"exactly one writer should win, got {results}"
    assert len(conflicts) == 1, f"exactly one should conflict, got {results}"


# ── Close / Sync 原子性竞态 (P0-7) ───────────────────────


def test_close_and_run_race_keeps_conversation_closed(session_factory):
    """已关闭会话在 100 个并发 sync 下不被复活（原子 UPDATE 兜底，P0-7）。

    模拟审查报告 P0-7 的竞态：close() 在旧 turn 运行期间发生，旧 turn 的
    sync_from_state 必须被原子 ``WHERE status <> 'CLOSED'`` UPDATE 拦截，
    CLOSED → ACTIVE 的复活次数必须为 0。真实并发由 PostgreSQL 行锁保证。
    """
    from types import SimpleNamespace

    actor_id = uuid4()
    community_id = uuid4()
    context = SimpleNamespace(actor_id=actor_id, community_id=community_id, house_ids=frozenset())

    service = ConversationService(session_factory)
    service.start(conversation_id="race-1", context=context, current_house_id=None)
    # 关闭会话
    service.close("race-1")
    assert service.get("race-1").is_closed is True

    state = _make_state("race-1")
    resurrected: list[int] = []
    errors: list[str] = []
    barrier = threading.Barrier(100)

    def worker() -> None:
        barrier.wait()
        try:
            snapshot = service.sync_from_state(state, waiting_confirm=False)
            if not snapshot.is_closed:
                resurrected.append(1)
        except AgentSessionError as exc:
            if exc.code != AgentSessionErrorCode.CONVERSATION_CLOSED.value:
                errors.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert errors == [], f"unexpected errors during race: {errors}"
    assert resurrected == [], f"CLOSED conversation was resurrected {len(resurrected)} times"
    # 关闭状态保持稳定。
    assert service.get("race-1").is_closed is True


# ── mark_handover lifecycle: CLOSED 为终态，禁止复活 ──────────────


def test_mark_handover_rejects_closed_conversation(session_factory):
    """已 CLOSED 的 conversation 调用 mark_handover 必须拒绝
    （CONVERSATION_CLOSED），不得出现 CLOSED -> HANDOVER 复活。"""
    from types import SimpleNamespace

    context = SimpleNamespace(actor_id=uuid4(), community_id=uuid4(), house_ids=frozenset())
    service = ConversationService(session_factory)
    service.start(conversation_id="pg-handover-closed", context=context, current_house_id=None)
    service.close("pg-handover-closed")
    assert service.get("pg-handover-closed").is_closed is True

    with pytest.raises(AgentSessionError) as exc:
        service.mark_handover("pg-handover-closed")
    assert exc.value.code == AgentSessionErrorCode.CONVERSATION_CLOSED


def test_close_vs_mark_handover_race_keeps_closed(session_factory):
    """并发 close 与 mark_handover：最终状态必须为 CLOSED，
    不得出现 CLOSED -> HANDOVER resurrection。

    mark_handover 使用原子条件 UPDATE ... WHERE status <> 'CLOSED'，
    因此无论哪个事务先提交，CLOSED 都不会被覆盖为 HANDOVER。
    """
    from types import SimpleNamespace

    context = SimpleNamespace(actor_id=uuid4(), community_id=uuid4(), house_ids=frozenset())
    cid = "pg-handover-race"
    setup = ConversationService(session_factory)
    setup.start(conversation_id=cid, context=context, current_house_id=None)

    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def closer() -> None:
        barrier.wait()
        try:
            ConversationService(session_factory).close(cid)
            results["close"] = "ok"
        except Exception as exc:  # noqa: BLE001
            results["close"] = repr(exc)

    def handover() -> None:
        barrier.wait()
        try:
            ConversationService(session_factory).mark_handover(cid)
            results["handover"] = "ok"
        except AgentSessionError as exc:
            results["handover"] = exc.code
        except Exception as exc:  # noqa: BLE001
            results["handover"] = repr(exc)

    t1 = threading.Thread(target=closer)
    t2 = threading.Thread(target=handover)
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    final = ConversationService(session_factory).get(cid)
    assert final.status == ConversationStatus.CLOSED.value, (
        f"final state must stay CLOSED, got {final.status}; race results={results}"
    )
