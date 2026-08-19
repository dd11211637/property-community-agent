"""Turn-level concurrency guards — P0 正确性底座。

封装 P0 涉及的三个动作供 runner 调用：

* :func:`read_turn_start_version` — turn 开始时读取 expected checkpoint 版本，
  严禁在 ``checkpointer.save`` 内部现读（否则 stale worker 读到最新版本，
  CAS 失效）。
* :func:`acquire_turn_lease` — 抢占会话 lease；被另一 live run 持有时抛
  ``AgentSessionError(CONVERSATION_BUSY)``。
* :func:`release_turn_lease` — 仅释放自己持有的 lease（防止误杀被新 run
  抢占的 lease）。

三个函数都接受 ``enforce_concurrency`` 开关；关闭时全部空操作，
  退回 legacy 单写者语义。
"""

from __future__ import annotations

from logging import getLogger
from uuid import UUID

from property_agent.agent.application.errors import (
    AgentSessionError,
    AgentSessionErrorCode,
)
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.run_lease import RunLeaseService

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
) -> None:
    """抢占会话 lease；被另一 live run 持有时抛 ``AgentSessionError(CONVERSATION_BUSY)``。"""
    if not enforce_concurrency or run_lease is None:
        return
    run_lease.acquire(thread_id, run_id=run_id)


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


__all__ = [
    "AgentSessionError",
    "AgentSessionErrorCode",
    "acquire_turn_lease",
    "read_turn_start_version",
    "release_turn_lease",
]