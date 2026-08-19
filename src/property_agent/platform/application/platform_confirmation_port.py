"""原子化确认消费端口 —— 业务模块共享的 ``PlatformConfirmationPort``。

业务 UoW 在同一 session 内调用本端口消费 ``agent_action_approvals``（审批）
+ ``confirmation_tokens``（令牌），确保：

* 审批消费**在 caller 的事务里**完成 → 业务 mutation / 审计 / Outbox / 审批消费
  同提交或同回滚，杜绝"已确认但未落库"或"已落库但未确认"的中间态。
* 令牌按既有规则消费作为纵深防御，避免上层仅靠 ``approval_ref`` 的伪造。
* P0-4 fencing：在消费前从 ``RequestContext.current().agent_lease`` 拿当前
  turn 的 lease，调用 ``assert_run_fence`` 校验当前 worker 仍拥有 conversation。
  stale worker 的业务写 100% 被拒绝（``StaleAgentRunError``）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from property_agent.agent.infrastructure.run_lease import Lease, assert_run_fence
from property_agent.platform.application.approval_service import ApprovalError, ApprovalService
from property_agent.platform.application.confirmation_service import ConfirmationService
from property_agent.platform.context import RequestContext
from property_agent.platform.domain.exceptions import InvalidConfirmationTokenException

ErrorFactory = Callable[[str, str, int], Any]


class PlatformConfirmationPort:
    """P0 原子化确认消费端口：fence 校验 → 审批 → 令牌，三段同事务。

    ``approval_ref`` 为空时只消费令牌（兼容未迁移调用方；P0 开启后所有受控写
    工具都应在命令里带上 ``approval_ref``）。

    ``error_factory`` 注入业务模块的 BusinessError 类，避免本平台层反向依赖
    业务域（保持依赖方向 platform ← application ← domain）。
    """

    def __init__(
        self,
        session: Session,
        approval_service: ApprovalService,
        *,
        error_factory: ErrorFactory,
    ) -> None:
        self._session = session
        self._approval_service = approval_service
        self._token_service = ConfirmationService(session)
        self._error_factory = error_factory

    def consume(
        self,
        *,
        approval_ref: str | None,
        token: str,
        actor_id: UUID,
        action: str,
        parameter_hash: str,
        request_id: str,
    ) -> None:
        # P0-4: 在任何 mutation / 审批消费之前，校验当前 turn 仍拥有 conversation
        # lease（fencing）。lease 从 trusted RequestContext 取，不由模型 slots 传入。
        # stale worker（lease 过期或被抢占）的业务写在此被拒绝。
        lease = _current_agent_lease()
        if lease is not None:
            assert_run_fence(self._session, lease)
        if approval_ref:
            try:
                self._approval_service.consume(
                    approval_id=UUID(approval_ref),
                    actor_id=actor_id,
                    action=action,
                    params_hash=parameter_hash,
                    session=self._session,
                )
            except ApprovalError as exc:
                raise self._error_factory(
                    f"CONFIRMATION_{exc.code}",
                    exc.message,
                    exc.status_code,
                ) from exc
        if not token or not token.strip():
            raise self._error_factory(
                "CONFIRMATION_REQUIRED",
                "This operation requires a confirmation token.",
                422,
            )
        try:
            self._token_service.consume(
                token=token,
                actor_id=actor_id,
                action=action,
                parameter_hash=parameter_hash,
                request_id=request_id,
            )
        except InvalidConfirmationTokenException as exc:
            raise self._error_factory("CONFIRMATION_INVALID", exc.message, 422) from exc


def _current_agent_lease() -> Lease | None:
    """从 trusted RequestContext 取当前 turn 的 lease（fencing token）。

    生产路径：runner acquire lease 后注入 ``RequestContext.agent_lease`` 并 activate。
    非请求 scope（测试 mock / 后台扫描）返回 None，fence check 退化为跳过。
    """
    ctx = RequestContext.current()
    if ctx is None or ctx.agent_lease is None:
        return None
    return Lease(
        thread_id=ctx.agent_lease.thread_id,
        run_id=ctx.agent_lease.run_id,
        fence=ctx.agent_lease.fence,
        lease_until=ctx.agent_lease.lease_until,
    )