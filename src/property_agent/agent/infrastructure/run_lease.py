"""运行 lease / fencing —— 同一 conversation 单写者（P0 正确性底座）。

一个 conversation（= thread_id）同一时刻只应有一个 live run 在跑长 LLM turn。
完整 turn 不宜持有数据库 row lock，因此用短事务 lease + fencing：

* 抢占：``INSERT … ON CONFLICT (thread_id) DO UPDATE … WHERE lease_until < now()
  RETURNING fence``。无返回行说明 lease 仍被另一 live run 持有，抛
  ``AgentConversationBusy``（409），前端可安全重试。
* 续期（heartbeat）：``UPDATE … SET lease_until = now() + ttl
  WHERE thread_id=:tid AND owner_run_id=:rid AND fence=:fence AND lease_until > now()
  RETURNING fence, lease_until``。无返回行说明 lease 已被抢占或过期，抛
  ``StaleAgentRun``。
* 释放：``DELETE … WHERE thread_id=:tid AND owner_run_id=:run_id``，只释放自己持有的
  lease，避免误杀已抢占的新 run。
* 业务写校验：``assert_run_fence(session, lease)`` 在业务 UoW 同一 session 内执行
  ``SELECT 1 … FOR SHARE``，0 行即 ``StaleAgentRun``——stale worker 的业务写被拒绝。

lease 与 checkpoint CAS 分工不同：
  run lease   → 防止同一 conversation 同时跑两个长 LLM turn；
  checkpoint CAS → 防止 lease 过期后的旧 worker 覆盖新 checkpoint；
  fence       → 防止 lease 过期后的旧 worker 执行业务 mutation（CAS 只挡 checkpoint）。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 延迟导入 AgentSessionError / AgentSessionErrorCode 以避免循环 import：
# run_lease → agent.application.errors → agent.application.__init__ → runner
# → turn_guard → run_lease（partially initialized）。errors 仅在 acquire 内使用。
if TYPE_CHECKING:
    pass

# 单 turn lease 时长：足够覆盖一次 LLM 调用，又短到过期后能快速抢占。
DEFAULT_LEASE_SECONDS = 30

SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class Lease:
    """一次 run 持有的 lease 快照（trusted runtime context 携带）。

    ``run_id`` + ``fence`` 必须原样传给业务写 UoW 的 ``assert_run_fence``，
    供其在 mutation 前校验当前 lease 仍属于本次 run。lease 过期或被抢占后，
    旧 worker 的业务写 100% 被拒绝（``StaleAgentRun``）。
    """

    thread_id: str
    run_id: UUID
    fence: int
    lease_until: datetime


class StaleAgentRunError(RuntimeError):
    """当前 worker 的 lease 已过期或被抢占，禁止执行任何业务 mutation。

    HTTP 409。触发场景：lease TTL 到期未续期、被另一 run 抢占、fence 不匹配。
    业务 UoW 在 mutation 前调用 ``assert_run_fence`` 检测到此情况必须立即抛出，
    不允许继续 commit。
    """

    status_code = 409

    def __init__(self, thread_id: str, *, reason: str = "lease expired or preempted") -> None:
        self.thread_id = thread_id
        self.reason = reason
        super().__init__(f"stale agent run for conversation {thread_id}: {reason}")


def _utcnow() -> datetime:
    """统一的 UTC 时间。SQLite 不存时区信息，比较时用 naive UTC 保持一致。"""
    return datetime.now(timezone.utc)


def _normalize(dt: datetime) -> datetime:
    """去掉 tzinfo 以兼容 SQLite naive datetime 存储。"""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class RunLeaseService:
    """基于 ``agent_run_leases`` 表的单写者 fencing 服务。"""

    def __init__(
        self, session_factory: SessionFactory, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> None:
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds

    # ── 抢占 / 续期 / 释放 ──────────────────────────────────────────

    def acquire(
        self,
        thread_id: str,
        *,
        run_id: UUID | None = None,
        ttl_seconds: int | None = None,
    ) -> Lease:
        """抢占（或续期）会话 lease，返回包含 fence 的 ``Lease``。

        返回前已提交事务。若会话已被另一 live run 持有则抛 ``AgentConversationBusy``。
        fence 单调递增：每次抢占 +1，旧 worker 凭旧 fence 的业务写会被
        ``assert_run_fence`` 拒绝。
        """
        run_id = run_id or uuid4()
        ttl = ttl_seconds or self._lease_seconds
        now = _utcnow()
        lease_until = now + timedelta(seconds=ttl)
        # 不使用 PostgreSQL 风格的 ``now()``：SQLite 没有这个函数，由 Python 端
        # 传入字面量值保证跨方言一致。生产 PostgreSQL 仍以应用 UTC 时间为准；
        # heartbeat / assert_run_fence 在数据库端用 ``lease_until >= :now`` 比较。
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
            RETURNING fence, lease_until
            """
        )
        session = self._session_factory()
        try:
            row = session.execute(
                statement,
                {
                    "thread_id": thread_id,
                    "owner_run_id": str(run_id),
                    "lease_until": _normalize(lease_until),
                    "updated_at": _normalize(now),
                    "now": _normalize(now),
                },
            ).fetchone()
            if row is None:
                # 冲突：现有 lease 未过期，另一 live run 持有本会话。
                # 延迟 import 避免循环依赖（见模块顶部注释）。
                from property_agent.agent.application.errors import (
                    AgentSessionError,
                    AgentSessionErrorCode,
                )

                raise AgentSessionError(
                    AgentSessionErrorCode.CONVERSATION_BUSY,
                    f"conversation {thread_id} is busy (lease held by another run)",
                )
            session.commit()
            fence = int(row[0])
            returned_until = row[1]
            if isinstance(returned_until, datetime):
                lease_until_out = (
                    returned_until.replace(tzinfo=timezone.utc)
                    if returned_until.tzinfo is None
                    else returned_until.astimezone(timezone.utc)
                )
            else:
                lease_until_out = lease_until
            return Lease(
                thread_id=thread_id,
                run_id=run_id,
                fence=fence,
                lease_until=lease_until_out,
            )
        finally:
            session.close()

    def renew(
        self,
        thread_id: str,
        run_id: UUID,
        fence: int,
        *,
        ttl_seconds: int | None = None,
    ) -> Lease:
        """续期（heartbeat）：必须校验 ``run_id + fence`` 仍属于当前 lease。

        无返回行（lease 已被抢占或过期）抛 ``StaleAgentRunError``，调用方必须立即
        取消当前 agent run，后续业务写也会被 ``assert_run_fence`` 拒绝。

        续期失败意味着当前 worker 已失去 ownership，不能继续执行任何 mutation。
        """
        ttl = ttl_seconds or self._lease_seconds
        now = _utcnow()
        new_until = now + timedelta(seconds=ttl)
        statement = text(
            """
            UPDATE agent_run_leases
            SET lease_until = :lease_until,
                updated_at  = :updated_at
            WHERE thread_id = :thread_id
              AND owner_run_id = :owner_run_id
              AND fence = :fence
              AND lease_until >= :now
            RETURNING fence, lease_until
            """
        )
        session = self._session_factory()
        try:
            row = session.execute(
                statement,
                {
                    "thread_id": thread_id,
                    "owner_run_id": str(run_id),
                    "fence": fence,
                    "lease_until": _normalize(new_until),
                    "updated_at": _normalize(now),
                    "now": _normalize(now),
                },
            ).fetchone()
            if row is None:
                raise StaleAgentRunError(
                    thread_id, reason="lease expired or preempted during heartbeat"
                )
            session.commit()
            returned_until = row[1]
            if isinstance(returned_until, datetime):
                lease_until_out = (
                    returned_until.replace(tzinfo=timezone.utc)
                    if returned_until.tzinfo is None
                    else returned_until.astimezone(timezone.utc)
                )
            else:
                lease_until_out = new_until
            return Lease(
                thread_id=thread_id,
                run_id=run_id,
                fence=int(row[0]),
                lease_until=lease_until_out,
            )
        finally:
            session.close()

    def release(self, thread_id: str, run_id: UUID) -> None:
        """释放自己持有的 lease（幂等；不释放被他人抢占的 lease）。

        P0-4 fencing: release **不删除行**，而是把 ``lease_until`` 设为过去时间。
        这样下次 acquire 走 ON CONFLICT UPDATE，fence 持续递增——stale worker
        持有的旧 fence 永远不会被新 run 复用，业务写的 ``assert_run_fence``
        能可靠拒绝 stale fence。
        """
        statement = text(
            """
            UPDATE agent_run_leases
            SET lease_until = :past,
                updated_at  = :now
            WHERE thread_id = :thread_id AND owner_run_id = :owner_run_id
            """
        )
        session = self._session_factory()
        try:
            now = _utcnow()
            session.execute(
                statement,
                {
                    "thread_id": thread_id,
                    "owner_run_id": str(run_id),
                    "past": _normalize(now - timedelta(seconds=1)),
                    "now": _normalize(now),
                },
            )
            session.commit()
        finally:
            session.close()

    # ── 只读探测 ────────────────────────────────────────────────────

    def is_held(self, thread_id: str) -> bool:
        """当前是否有未过期的 lease（只读探测，用于恢复扫描/诊断）。"""
        statement = text(
            "SELECT 1 FROM agent_run_leases WHERE thread_id = :tid AND lease_until >= :now"
        )
        session = self._session_factory()
        try:
            return (
                session.execute(statement, {"tid": thread_id, "now": _normalize(_utcnow())}).first()
                is not None
            )
        finally:
            session.close()


def assert_run_fence(session: Session, lease: Lease) -> None:
    """在业务写 UoW 同一 session 内校验当前 lease 仍属于本次 run。

    使用 ``UPDATE … SET updated_at = updated_at … RETURNING 1`` 作为行锁
    （跨方言兼容：PostgreSQL 和 SQLite 都支持），确保：
    * lease 行存在且 ``owner_run_id == lease.run_id``；
    * ``fence == lease.fence``（旧 worker 凭旧 fence 被拒绝）；
    * ``lease_until > now()``（lease 未过期）。

    0 行返回即抛 ``StaleAgentRunError``，业务 UoW 必须回滚，不允许继续 mutation。
    行锁防止校验后 lease 被抢占（acquire/renew/release 都会等待此锁释放）。

    此函数必须在业务 mutation **之前**调用（通常在 ``PlatformConfirmationPort.consume``
    内，覆盖所有受控写路径）。
    """
    statement = text(
        """
        UPDATE agent_run_leases
        SET updated_at = updated_at
        WHERE thread_id = :thread_id
          AND owner_run_id = :owner_run_id
          AND fence = :fence
          AND lease_until >= :now
        RETURNING 1
        """
    )
    row = session.execute(
        statement,
        {
            "thread_id": lease.thread_id,
            "owner_run_id": str(lease.run_id),
            "fence": lease.fence,
            "now": _normalize(_utcnow()),
        },
    ).first()
    if row is None:
        raise StaleAgentRunError(
            lease.thread_id, reason="fence check failed (expired, preempted, or mismatched)"
        )


class LeaseHeartbeat:
    """后台 lease 续期（P0-5 heartbeat）。

    长 LLM turn 期间在后台线程每 ``interval_seconds`` 调用 ``RunLeaseService.renew``
    续期一次，防止 lease TTL 到期后被另一 run 抢占。若某次续期失败（lease 已过期
    或被抢占），标记 ``stale`` 并停止循环——持有该 lease 的 worker 必须在
    ``stale`` 变为 True 后立即中止当前 run（``StaleAgentRunError``），其后续业务写
    也会被 ``assert_run_fence`` 拒绝。

    生命周期：runner 在 ``_plan_start`` / ``_plan_resume`` 拿到 lease 后 ``start()``，
    turn 结束（或异常）时在 ``finally`` 中 ``stop()`` 释放后再 ``release``。
    """

    def __init__(
        self,
        run_lease: RunLeaseService,
        lease: Lease,
        *,
        interval_seconds: int = 10,
        on_stale: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._run_lease = run_lease
        self._lease = lease
        self._interval = interval_seconds
        self._on_stale = on_stale
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stale = False
        self._error: BaseException | None = None

    @property
    def stale(self) -> bool:
        with self._lock:
            return self._stale

    @property
    def error(self) -> BaseException | None:
        with self._lock:
            return self._error

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._renew_loop, daemon=True, name="lease-heartbeat"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)

    def _renew_loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._lease = self._run_lease.renew(
                    self._lease.thread_id, self._lease.run_id, self._lease.fence
                )
            except StaleAgentRunError as exc:
                self._mark_stale(exc)
                return
            except Exception as exc:  # pragma: no cover - 任何续期异常都视为失租
                self._mark_stale(exc)
                return

    def _mark_stale(self, exc: BaseException) -> None:
        with self._lock:
            self._stale = True
            self._error = exc
        if self._on_stale is not None:
            try:
                self._on_stale(exc)
            except Exception:
                logger.exception("on_stale callback raised")


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "Lease",
    "LeaseHeartbeat",
    "RunLeaseService",
    "SessionFactory",
    "StaleAgentRunError",
    "assert_run_fence",
]
