"""持久化 Checkpointer — PRD §6.5.8。

单元测试可以用 ``MemoryCheckpointer``；**演示与生产环境必须用本实现**，
否则应用重启后待确认流程无法恢复。

要点：

* ``thread_id`` 使用稳定的 ``conversation_id``，不使用随机 run id；
* 快照写入前用 ``canonical_payload`` 归一化（UUID/datetime/Decimal/Enum → 字符串），
  保证 JSON 列可写、且**跨进程重启反序列化后幂等键保持不变**；
* 每个线程只保留最新一版快照并递增 ``version``，恢复时读最新版；
* 检查点写入走独立事务，与业务写事务解耦——检查点不是业务凭据。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_agent.agent.infrastructure.models import AgentCheckpointModel
from property_agent.agent.state import GraphState
from property_agent.platform.application.hashing import canonical_payload

SessionFactory = Callable[[], Session]


class CheckpointVersionConflict(Exception):
    """CAS 失败：检查点版本已被其他写入者抢先更新。

    语义上表示本 run 拿到的快照已经 stale（例如 lease 过期后被新 run 覆盖）。
    调用方应**终止本 stale run**，而不是用旧状态覆盖新 checkpoint。
    """

    def __init__(self, thread_id: str, expected: int) -> None:
        self.thread_id = thread_id
        self.expected = expected
        super().__init__(f"checkpoint version conflict for thread {thread_id}: expected {expected}")


@dataclass(frozen=True)
class LangGraphCheckpointCursor:
    """v2 接受头指针：仅存 LangGraph 内部 checkpoint 的定位符，绝不作为业务/信任权威。

    只保存精确支持的定位符（通常是 thread_id / checkpoint_ns / checkpoint_id）。
    不持久化 actor / roles / community / house / lease / fence / approval /
    confirmation token / runtime policy。
    """

    thread_id: str
    checkpoint_ns: str | None = None
    checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "checkpoint_ns": self.checkpoint_ns,
            "checkpoint_id": self.checkpoint_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LangGraphCheckpointCursor | None":
        if not data:
            return None
        if set(data) - {"thread_id", "checkpoint_ns", "checkpoint_id"}:
            raise ValueError("runtime cursor contains unsupported fields")
        cursor = cls(
            thread_id=str(data.get("thread_id", "")),
            checkpoint_ns=data.get("checkpoint_ns"),
            checkpoint_id=data.get("checkpoint_id"),
        )
        if not cursor.thread_id or not cursor.checkpoint_id:
            raise ValueError("runtime cursor requires thread_id and checkpoint_id")
        return cursor


@dataclass(frozen=True)
class AcceptedCheckpoint:
    """应用接受头记录：恢复时读取的权威连续性来源。"""

    state: GraphState
    version: int
    runtime_cursor: LangGraphCheckpointCursor | None
    interrupt_node: str | None
    pending_confirm: bool


def _snapshot(state: GraphState) -> dict[str, Any]:
    """把 GraphState 转成 JSON 安全的快照。"""
    return canonical_payload(state.to_dict())


class SqlAlchemyCheckpointer:
    """基于 SQLAlchemy 的持久化检查点存储（实现 ``Checkpointer`` 协议）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # ---- Checkpointer 协议 ----

    def save(
        self,
        thread_id: str,
        state: GraphState,
        *,
        expected_version: int | None = None,
        runtime_cursor: dict[str, Any] | None = None,
    ) -> None:
        """持久化快照。

        ``expected_version`` 传入时走 CAS：
        * ``expected_version == 0``（无 checkpoint 的首发）——原子 ``INSERT … ON CONFLICT
          (thread_id) DO NOTHING RETURNING version``；两个竞争的首发者只有一方插入成功，
          另一方返回 0 行 → ``CheckpointVersionConflict``（关闭 FIRST_CHECKPOINT_CAS_GAP）。
        * ``expected_version > 0``——``UPDATE … WHERE version=:expected RETURNING version``，
          0 行则抛 ``CheckpointVersionConflict``。该值必须由调用方在 **turn 开始** 时读取
          并传入——绝不能在 save 内部现读，否则 stale worker 会读到最新版本导致 CAS 失效。

        ``runtime_cursor`` 仅在 v2 路径提供，是应用接受头指向 LangGraph 内部 checkpoint
        的精确定位符；v1 路径为 ``None``。

        ``expected_version`` 为 ``None``（旧调用方 / 测试直接调用）时回退到
        SELECT→+1→COMMIT；此时由 run lease 保证单写者，不会出现并发竞争。生产共享
        lifecycle 不得传 ``None``。
        """
        payload = _snapshot(state)
        pending = bool(state.pending_action) and state._interrupt_node is not None
        session = self._session_factory()
        try:
            if expected_version is not None:
                self._save_cas(
                    session,
                    thread_id,
                    payload,
                    pending,
                    state._interrupt_node,
                    expected_version,
                    runtime_cursor,
                )
            else:
                self._save_legacy(
                    session, thread_id, payload, pending, state._interrupt_node, runtime_cursor
                )
            session.commit()
        finally:
            session.close()

    @staticmethod
    def _save_cas(
        session: Session,
        thread_id: str,
        payload: dict[str, Any],
        pending: bool,
        interrupt_node: str | None,
        expected: int,
        runtime_cursor: dict[str, Any] | None,
    ) -> None:
        if expected == 0:
            values = {
                "thread_id": thread_id,
                "version": 1,
                "state": payload,
                "interrupt_node": interrupt_node,
                "pending_confirm": pending,
                "runtime_cursor": runtime_cursor,
            }
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            elif dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
            else:
                dialect_insert = None
            if dialect_insert is not None:
                row = session.execute(
                    dialect_insert(AgentCheckpointModel)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["thread_id"])
                    .returning(AgentCheckpointModel.version)
                ).scalar_one_or_none()
                if row is None:
                    raise CheckpointVersionConflict(thread_id, expected)
                return
            try:
                session.execute(insert(AgentCheckpointModel).values(**values))
                session.flush()
            except IntegrityError:
                session.rollback()
                raise CheckpointVersionConflict(thread_id, expected) from None
            return
        stmt = (
            update(AgentCheckpointModel)
            .where(
                AgentCheckpointModel.thread_id == thread_id,
                AgentCheckpointModel.version == expected,
            )
            .values(
                version=AgentCheckpointModel.version + 1,
                state=payload,
                interrupt_node=interrupt_node,
                pending_confirm=pending,
                runtime_cursor=runtime_cursor,
            )
            .returning(AgentCheckpointModel.version)
        )
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            raise CheckpointVersionConflict(thread_id, expected)

    @staticmethod
    def _save_legacy(
        session: Session,
        thread_id: str,
        payload: dict[str, Any],
        pending: bool,
        interrupt_node: str | None,
        runtime_cursor: dict[str, Any] | None,
    ) -> None:
        record = session.execute(
            select(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == thread_id)
        ).scalar_one_or_none()
        if record is None:
            session.add(
                AgentCheckpointModel(
                    thread_id=thread_id,
                    version=1,
                    state=payload,
                    interrupt_node=interrupt_node,
                    pending_confirm=pending,
                    runtime_cursor=runtime_cursor,
                )
            )
        else:
            record.version = record.version + 1
            record.state = payload
            record.interrupt_node = interrupt_node
            record.pending_confirm = pending
            record.runtime_cursor = runtime_cursor

    def load(self, thread_id: str) -> GraphState | None:
        session = self._session_factory()
        try:
            record = session.execute(
                select(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == thread_id)
            ).scalar_one_or_none()
            if record is None:
                return None
            return GraphState.from_dict(dict(record.state))
        finally:
            session.close()

    def list_threads(self) -> list[str]:
        session = self._session_factory()
        try:
            rows = session.execute(select(AgentCheckpointModel.thread_id)).scalars().all()
            return list(rows)
        finally:
            session.close()

    # ---- 恢复辅助 ----

    def pending_threads(self) -> list[str]:
        """返回所有仍停在确认中断上的会话（重启后可用于恢复扫描）。"""
        session = self._session_factory()
        try:
            rows = (
                session.execute(
                    select(AgentCheckpointModel.thread_id).where(
                        AgentCheckpointModel.pending_confirm.is_(True)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)
        finally:
            session.close()

    def version_of(self, thread_id: str) -> int:
        """返回当前接受头版本；无 checkpoint 时返回 0（accepted version 0）。

        生产 lifecycle 始终读取 0 / 1 / 2 / …：无 checkpoint 即 accepted version 0，
        首次发布以 ``expected_version=0`` 走原子 INSERT CAS（见 ``save``）。
        """
        session = self._session_factory()
        try:
            version = session.execute(
                select(AgentCheckpointModel.version).where(
                    AgentCheckpointModel.thread_id == thread_id
                )
            ).scalar()
            return version if version is not None else 0
        finally:
            session.close()

    def load_accepted(self, thread_id: str) -> AcceptedCheckpoint | None:
        """读取应用接受头记录（权威连续性来源）。无记录返回 ``None``。"""
        record = self._load_record(thread_id)
        if record is None:
            return None
        return AcceptedCheckpoint(
            state=GraphState.from_dict(dict(record.state)),
            version=record.version,
            runtime_cursor=LangGraphCheckpointCursor.from_dict(record.runtime_cursor),
            interrupt_node=record.interrupt_node,
            pending_confirm=bool(record.pending_confirm),
        )

    def publish_accepted(
        self,
        thread_id: str,
        state: GraphState,
        *,
        expected_version: int,
        runtime_cursor: dict[str, Any] | None = None,
    ) -> None:
        """原子发布应用接受头：state + runtime_cursor + pending_confirm + interrupt 元数据
        + version 一起随 CAS 提交。``expected_version`` 必须来自 turn 开始时的读取，且
        不得为 ``None``（生产共享 lifecycle 强制）。
        """
        if expected_version is None:
            raise ValueError(
                "publish_accepted requires a concrete expected_version (0 for first publish)"
            )
        cursor = LangGraphCheckpointCursor.from_dict(runtime_cursor)
        self.save(
            thread_id,
            state,
            expected_version=expected_version,
            runtime_cursor=cursor.to_dict() if cursor else None,
        )

    def _load_record(self, thread_id: str) -> Any | None:
        session = self._session_factory()
        try:
            return session.execute(
                select(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == thread_id)
            ).scalar_one_or_none()
        finally:
            session.close()

    def discard(self, thread_id: str) -> None:
        """丢弃线程快照（会话关闭或待确认作废后清理）。"""
        session = self._session_factory()
        try:
            record = session.execute(
                select(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == thread_id)
            ).scalar_one_or_none()
            if record is not None:
                session.delete(record)
                session.commit()
        finally:
            session.close()
