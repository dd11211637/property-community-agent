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
from typing import Any

from sqlalchemy import select, update
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
    ) -> None:
        """持久化快照。

        ``expected_version`` 传入时走 CAS：``UPDATE … WHERE version=:expected
        RETURNING version``，0 行则抛 ``CheckpointVersionConflict``。这个值必须由
        调用方在 **turn 开始** 时读取并传入——绝不能在 save 内部现读，否则 stale
        worker 会读到最新版本导致 CAS 失效。

        ``expected_version`` 为 ``None``（新线程 / 旧调用方 / 测试直接调用）时回退到
        SELECT→+1→COMMIT；此时由 run lease 保证单写者，不会出现并发竞争。
        """
        payload = _snapshot(state)
        pending = bool(state.pending_action) and state._interrupt_node is not None
        session = self._session_factory()
        try:
            if expected_version is not None:
                self._save_cas(
                    session, thread_id, payload, pending, state._interrupt_node, expected_version
                )
            else:
                self._save_legacy(session, thread_id, payload, pending, state._interrupt_node)
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
    ) -> None:
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
                )
            )
        else:
            record.version = record.version + 1
            record.state = payload
            record.interrupt_node = interrupt_node
            record.pending_confirm = pending

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

    def version_of(self, thread_id: str) -> int | None:
        session = self._session_factory()
        try:
            return session.execute(
                select(AgentCheckpointModel.version).where(
                    AgentCheckpointModel.thread_id == thread_id
                )
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
