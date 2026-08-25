"""Turn-level concurrency guards — P0 正确性底座。

封装 P0 涉及的三个动作供 runner 调用：

* :func:`read_turn_start_version` — turn 开始时读取 expected checkpoint 版本，
  严禁在 ``checkpointer.save`` 内部现读（否则 stale worker 读到最新版本，
  CAS 失效）。**必须在 acquire lease 之后调用**，避免读到被新 run 覆盖的版本。
* :func:`acquire_turn_lease` — 抢占会话 lease，返回包含 fence 的 ``Lease``；
  被另一 live run 持有时抛 ``AgentSessionError(CONVERSATION_BUSY)``。
* :func:`release_turn_lease` — 仅释放自己持有的 lease（防止误杀被新 run
  抢占的 lease）。
* :func:`heartbeat_turn_lease` — 续期 lease（必须校验 run_id + fence）；
  失败抛 ``StaleAgentRunError``，调用方必须立即终止当前 run。

三个函数都接受 ``enforce_concurrency`` 开关；关闭时全部空操作，
  退回 legacy 单写者语义。
"""

from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from time import perf_counter
from typing import TYPE_CHECKING, Any

from property_agent.agent.application.errors import (
    AgentSessionError,
    AgentSessionErrorCode,
)
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.run_lease import (
    Lease,
    LeaseHeartbeat,
    RunLeaseService,
    StaleAgentRunError,
)

if TYPE_CHECKING:
    from uuid import UUID

logger = getLogger(__name__)


def read_turn_start_version(
    checkpointer: SqlAlchemyCheckpointer | None,
    *,
    enforce_concurrency: bool,
    thread_id: str,
) -> int | None:
    """读取 turn 开始时的 checkpoint 版本（作为本轮 CAS 的 expected_version）。

    绝不能在 ``checkpointer.save`` 内部现读——否则 stale worker 会读到最新版本
    导致 CAS 失效。护栏关闭或无 checkpointer 时返回 ``None``（退化为 legacy 保存）。

    **必须在 ``acquire_turn_lease`` 之后调用**：先拿到 lease ownership 再读版本，
    避免读到被并发新 run 覆盖的版本（审查报告 P0-3）。
    """
    if not enforce_concurrency or checkpointer is None:
        return None
    return checkpointer.version_of(thread_id)


def acquire_turn_lease(
    run_lease: RunLeaseService | None,
    *,
    enforce_concurrency: bool,
    thread_id: str,
    run_id: UUID,
) -> Lease | None:
    """抢占会话 lease，返回 ``Lease``（含 fence）；被另一 live run 持有时抛
    ``AgentSessionError(CONVERSATION_BUSY)``。

    返回的 ``Lease`` 携带 ``run_id`` + ``fence``，必须原样注入 trusted runtime
    context（``RequestContext.agent_lease``），供业务写 UoW 在 mutation 前校验
    （``assert_run_fence``）。
    """
    if not enforce_concurrency or run_lease is None:
        return None
    return run_lease.acquire(thread_id, run_id=run_id)


def heartbeat_turn_lease(
    run_lease: RunLeaseService | None,
    *,
    lease: Lease | None,
) -> Lease | None:
    """续期 lease（heartbeat）：必须校验 ``run_id + fence`` 仍属于当前 lease。

    无返回行（lease 已被抢占或过期）抛 ``StaleAgentRunError``，调用方必须立即
    取消当前 agent run——后续业务写也会被 ``assert_run_fence`` 拒绝。

    续期失败意味着当前 worker 已失去 ownership，不能继续执行任何 mutation。
    """
    if lease is None or run_lease is None:
        return lease
    return run_lease.renew(lease.thread_id, lease.run_id, lease.fence)


def release_turn_lease(
    run_lease: RunLeaseService | None,
    *,
    thread_id: str,
    run_id: UUID,
) -> None:
    """释放自己持有的 lease（释放失败仅记日志，不掩盖业务结果）。"""
    if run_lease is None:
        return
    try:
        run_lease.release(thread_id, run_id)
    except Exception as exc:  # pragma: no cover — 释放失败通常是 DB 抖动
        logger.warning("failed to release run lease for %s: %s", thread_id, exc)


def activate_lease_context(context: Any, lease: Lease | None) -> Any:
    """把 ``run_id + fence`` 注入 trusted ``RequestContext``（P0-4 fencing）。

    业务写 UoW 通过 ``RequestContext.current().agent_lease`` 拿到 lease，
    在 mutation 前调用 ``assert_run_fence(session, lease)`` 校验当前 turn
    仍拥有 conversation。非 ``RequestContext`` 实例（测试 mock）不注入，
    业务 UoW 的 fence check 退化为跳过（仅在测试环境）。
    """
    from dataclasses import replace

    from property_agent.agent.selector_context import activate_selector_context
    from property_agent.platform.context import AgentLeaseContext, ExecutionSource, RequestContext

    activate_selector_context(context)

    agent_lease = None
    if lease is not None:
        agent_lease = AgentLeaseContext(
            thread_id=lease.thread_id,
            run_id=lease.run_id,
            fence=lease.fence,
            lease_until=lease.lease_until,
        )
    try:
        new_context = replace(
            context,
            agent_lease=agent_lease,
            execution_source=ExecutionSource.AGENT,
        )
    except TypeError:
        return context
    if isinstance(new_context, RequestContext):
        new_context.activate()
    return new_context


class TurnLeaseController:
    """Mechanical lease/heartbeat facade used by the lifecycle coordinator."""

    def __init__(
        self,
        run_lease: Callable[[], RunLeaseService | None],
        checkpointer: SqlAlchemyCheckpointer | None,
        *,
        enforce: Callable[[], bool],
        heartbeat_interval_seconds: int,
        observability: Any | None = None,
    ) -> None:
        self._run_lease = run_lease
        self._checkpointer = checkpointer
        self._enforce = enforce
        self._heartbeat_interval = heartbeat_interval_seconds
        self._telemetry = observability

    def _service(self) -> RunLeaseService | None:
        return self._run_lease()

    def acquire(self, thread_id: str, run_id: UUID) -> Lease | None:
        started = perf_counter()
        try:
            lease = acquire_turn_lease(
                self._service(),
                enforce_concurrency=self._enforce(),
                thread_id=thread_id,
                run_id=run_id,
            )
        except AgentSessionError:
            self._record("acquire", "contention", started)
            raise
        except Exception:
            self._record("acquire", "failed", started)
            raise
        self._record("acquire", "success" if lease is not None else "disabled", started)
        return lease

    def renew(self, lease: Lease | None) -> Lease | None:
        started = perf_counter()
        try:
            renewed = heartbeat_turn_lease(self._service(), lease=lease)
        except StaleAgentRunError:
            self._record("renew", "lost", started)
            raise
        except Exception:
            self._record("renew", "failed", started)
            raise
        self._record("renew", "success" if lease is not None else "disabled", started)
        return renewed

    def release(self, thread_id: str, lease: Lease | None) -> None:
        if lease is not None:
            started = perf_counter()
            release_turn_lease(self._service(), thread_id=thread_id, run_id=lease.run_id)
            self._record("release", "completed", started)

    def activate(self, context: Any, lease: Lease | None) -> Any:
        return activate_lease_context(context, lease)

    def version(self, thread_id: str) -> int | None:
        started = perf_counter()
        try:
            version = read_turn_start_version(
                self._checkpointer,
                enforce_concurrency=self._enforce(),
                thread_id=thread_id,
            )
        except Exception:
            self._record("checkpoint_read", "failed", started)
            raise
        self._record("checkpoint_read", "success" if version is not None else "absent", started)
        return version

    def _record(self, operation: str, outcome: str, started: float) -> None:
        if self._telemetry is None:
            return
        attributes = {"operation": operation, "outcome": outcome}
        self._telemetry.count("agent_lease_operation_total", attributes=attributes)
        self._telemetry.duration(
            "agent_lease_operation_duration_seconds",
            perf_counter() - started,
            attributes=attributes,
        )

    def start_heartbeat(self, lease: Lease | None) -> LeaseHeartbeat | None:
        service = self._service()
        if lease is None or service is None:
            return None
        heartbeat = LeaseHeartbeat(service, lease, interval_seconds=self._heartbeat_interval)
        heartbeat.start()
        return heartbeat

    @staticmethod
    def stop_heartbeat(heartbeat: LeaseHeartbeat | None) -> None:
        if heartbeat is not None:
            heartbeat.stop()

    @staticmethod
    def assert_alive(lease: Lease | None, heartbeat: LeaseHeartbeat | None) -> None:
        if heartbeat is not None and heartbeat.stale:
            thread_id = lease.thread_id if lease is not None else "<unknown>"
            raise StaleAgentRunError(
                thread_id, reason="lease heartbeat detected stale run; aborting turn"
            )


__all__ = [
    "AgentSessionError",
    "AgentSessionErrorCode",
    "acquire_turn_lease",
    "activate_lease_context",
    "heartbeat_turn_lease",
    "read_turn_start_version",
    "release_turn_lease",
    "TurnLeaseController",
]
