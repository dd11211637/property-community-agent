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

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.agent.infrastructure.models import AgentCheckpointModel
from property_agent.agent.state import GraphState
from property_agent.platform.application.hashing import canonical_payload

SessionFactory = Callable[[], Session]


def _snapshot(state: GraphState) -> dict[str, Any]:
    """把 GraphState 转成 JSON 安全的快照。"""
    return canonical_payload(state.to_dict())


class SqlAlchemyCheckpointer:
    """基于 SQLAlchemy 的持久化检查点存储（实现 ``Checkpointer`` 协议）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # ---- Checkpointer 协议 ----

    def save(self, thread_id: str, state: GraphState) -> None:
        payload = _snapshot(state)
        pending = bool(state.pending_action) and state._interrupt_node is not None
        session = self._session_factory()
        try:
            record = session.execute(
                select(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == thread_id)
            ).scalar_one_or_none()
            if record is None:
                session.add(
                    AgentCheckpointModel(
                        thread_id=thread_id,
                        version=1,
                        state=payload,
                        interrupt_node=state._interrupt_node,
                        pending_confirm=pending,
                    )
                )
            else:
                record.version = record.version + 1
                record.state = payload
                record.interrupt_node = state._interrupt_node
                record.pending_confirm = pending
            session.commit()
        finally:
            session.close()

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
