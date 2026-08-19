"""运行 lease / fencing —— 同一 conversation 单写者（P0 正确性底座）。

一个 conversation（= thread_id）同一时刻只应有一个 live run 在跑长 LLM turn。
完整 turn 不宜持有数据库 row lock，因此用短事务 lease + fencing：

* 抢占：``INSERT … ON CONFLICT (thread_id) DO UPDATE … WHERE lease_until < now()
  RETURNING fence``。无返回行说明 lease 仍被另一 live run 持有，抛
  ``AgentConversationBusy``（409），前端可安全重试。
* 释放：``DELETE … WHERE thread_id=:tid AND owner_run_id=:run_id``，只释放自己持有的
  lease，避免误杀已抢占的新 run。

lease 与 checkpoint CAS 分工不同：
  run lease   → 防止同一 conversation 同时跑两个长 LLM turn；
  checkpoint CAS → 防止 lease 过期后的旧 worker 覆盖新 checkpoint。
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from property_agent.agent.application.errors import AgentSessionError, AgentSessionErrorCode

# 单 turn lease 时长：足够覆盖一次 LLM 调用，又短到过期后能快速抢占。
DEFAULT_LEASE_SECONDS = 30


class RunLeaseService:
    """基于 ``agent_run_leases`` 表的单写者 fencing 服务。"""

    def __init__(self, session_factory: Any, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds

    def acquire(
        self,
        thread_id: str,
        *,
        run_id: UUID | None = None,
        ttl_seconds: int | None = None,
    ) -> UUID:
        """抢占（或续期）会话 lease，返回本次运行的 run_id。

        返回前已提交事务。若会话已被另一 live run 持有则抛 ``AgentConversationBusy``。
        """
        run_id = run_id or uuid4()
        ttl = ttl_seconds or self._lease_seconds
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=ttl)
        # 不使用 PostgreSQL 风格的 ``now()``：SQLite 没有这个函数，由 Python 端
        # 传入字面量值保证跨方言一致。
        statement = text(
            """
            INSERT INTO agent_run_leases (thread_id, owner_run_id, lease_until, fence, updated_at)
            VALUES (:thread_id, :owner_run_id, :lease_until, 1, :updated_at)
            ON CONFLICT (thread_id) DO UPDATE SET
                owner_run_id = excluded.owner_run_id,
                lease_until  = excluded.lease_until,
                fence        = agent_run_leases.fence + 1,
                updated_at   = :updated_at
            WHERE agent_run_leases.lease_until < :now
            RETURNING fence
            """
        )
        session = self._session_factory()
        try:
            row = session.execute(
                statement,
                {
                    "thread_id": thread_id,
                    "owner_run_id": str(run_id),
                    "lease_until": lease_until,
                    "updated_at": now,
                    "now": now,
                },
            ).fetchone()
            if row is None:
                # 冲突：现有 lease 未过期，另一 live run 持有本会话。
                raise AgentSessionError(
                    AgentSessionErrorCode.CONVERSATION_BUSY,
                    f"conversation {thread_id} is busy (lease held by another run)",
                )
            session.commit()
            return run_id
        finally:
            session.close()

    def release(self, thread_id: str, run_id: UUID) -> None:
        """释放自己持有的 lease（幂等；不释放被他人抢占的 lease）。"""
        statement = text(
            """
            DELETE FROM agent_run_leases
            WHERE thread_id = :thread_id AND owner_run_id = :owner_run_id
            """
        )
        session = self._session_factory()
        try:
            session.execute(
                statement,
                {"thread_id": thread_id, "owner_run_id": str(run_id)},
            )
            session.commit()
        finally:
            session.close()

    def is_held(self, thread_id: str) -> bool:
        """当前是否有未过期的 lease（只读探测，用于恢复扫描/诊断）。"""
        statement = text(
            "SELECT 1 FROM agent_run_leases WHERE thread_id = :tid AND lease_until >= :now"
        )
        session = self._session_factory()
        try:
            return (
                session.execute(
                    statement, {"tid": thread_id, "now": datetime.now(timezone.utc)}
                ).first()
                is not None
            )
        finally:
            session.close()
