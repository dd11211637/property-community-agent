"""动作审批服务 —— P0 确认原子性（deep-research-report.md §Approval 原子化）。

确认令牌（``confirmation_tokens``）只是传输层防伪造凭据；真正的**业务审批**是
``agent_action_approvals`` 记录，生命周期：

    PENDING → APPROVED → CONSUMED
                  ↘ REJECTED
       （过期）↘ EXPIRED

关键不变量：**CONSUMED 必须与业务 mutation / 审计 / Outbox 落在同一个
Session/UnitOfWork 事务里**。``consume`` 因此接受调用方传入的 ``session``，
在该会话内 ``get_for_update`` 后校验并落 ``CONSUMED``，由调用方随后统一 commit：
要么业务副作用与审批消费一起提交，要么一起回滚，杜绝中间态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.agent.infrastructure.models import AgentActionApprovalModel
from property_agent.platform.application.hashing import canonical_hash

# 确认有效期：与既有 ConfirmationService 的 5 分钟 TTL 对齐（PF-04）。
DEFAULT_APPROVAL_TTL_MINUTES = 5


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"


class ApprovalError(Exception):
    """审批校验失败（actor 不符 / 参数变化 / 已过期 / 已消费 / 重复确认等）。"""

    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class Approval:
    """审批记录快照（脱离 ORM 会话后安全传递）。"""

    id: UUID
    conversation_id: str
    actor_id: UUID
    action: str
    params_hash: str
    status: ApprovalStatus
    expires_at: datetime
    approved_at: datetime | None
    consumed_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)

    @classmethod
    def from_model(cls, m: AgentActionApprovalModel) -> Approval:
        return cls(
            id=m.id,
            conversation_id=m.conversation_id,
            actor_id=m.actor_id,
            action=m.action,
            params_hash=m.params_hash,
            status=ApprovalStatus(m.status),
            expires_at=m.expires_at,
            approved_at=m.approved_at,
            consumed_at=m.consumed_at,
        )


class ApprovalService:
    """受控写操作的审批记录管理。"""

    def __init__(
        self,
        session_factory: Any,
        *,
        ttl_minutes: int = DEFAULT_APPROVAL_TTL_MINUTES,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_minutes = ttl_minutes

    # ── 写路径：创建 PENDING ──────────────────────────────────────

    def create_pending(
        self,
        *,
        conversation_id: str,
        actor_id: UUID,
        action: str,
        params: dict[str, Any],
        session: Session | None = None,
    ) -> Approval:
        """为一次待确认操作创建 PENDING 审批。

        同一会话 + 同一动作 + 同一参数指纹，若已存在开放（PENDING/APPROVED）审批则
        复用之（部分唯一索引保证原子性，避免重复确认凭空产生第二个业务对象）。
        返回前在传入 session（或新建 session）内提交。
        """
        params_hash = canonical_hash(params)
        owned = session is not None
        s = session or self._session_factory()
        try:
            existing = (
                s.execute(
                    select(AgentActionApprovalModel)
                    .where(
                        AgentActionApprovalModel.conversation_id == conversation_id,
                        AgentActionApprovalModel.action == action,
                        AgentActionApprovalModel.params_hash == params_hash,
                        AgentActionApprovalModel.status.in_(
                            [ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value]
                        ),
                    )
                    .with_for_update()
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return Approval.from_model(existing)
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=self._ttl_minutes)
            model = AgentActionApprovalModel(
                conversation_id=conversation_id,
                actor_id=actor_id,
                action=action,
                params_hash=params_hash,
                status=ApprovalStatus.PENDING.value,
                expires_at=expires_at,
            )
            s.add(model)
            s.flush()
            approval = Approval.from_model(model)
            if not owned:
                s.commit()
            return approval
        finally:
            if not owned:
                s.close()

    # ── 确认路径：PENDING → APPROVED ─────────────────────────────

    def approve(self, *, approval_id: UUID, actor_id: UUID) -> Approval:
        """用户点击确认：PENDING → APPROVED。独立短事务。"""
        s = self._session_factory()
        try:
            model = s.execute(
                select(AgentActionApprovalModel)
                .where(AgentActionApprovalModel.id == approval_id)
                .with_for_update()
            ).scalar_one_or_none()
            if model is None:
                raise ApprovalError("APPROVAL_NOT_FOUND", "审批记录不存在。", status_code=404)
            if model.status != ApprovalStatus.PENDING.value:
                raise ApprovalError(
                    "APPROVAL_NOT_PENDING",
                    f"审批已处于 {model.status} 状态，无法再次确认。",
                )
            if model.actor_id != actor_id:
                raise ApprovalError("APPROVAL_ACTOR_MISMATCH", "确认人与会话所有者不一致。")
            if _is_expired(model.expires_at):
                model.status = ApprovalStatus.EXPIRED.value
                s.commit()
                raise ApprovalError("APPROVAL_EXPIRED", "确认已超时失效，请重新发起。")
            model.status = ApprovalStatus.APPROVED.value
            model.approved_at = datetime.now(timezone.utc)
            s.commit()
            return Approval.from_model(model)
        finally:
            s.close()

    # ── 执行路径：APPROVED/PENDING → CONSUMED（与业务 mutation 同事务） ──

    def consume(
        self,
        *,
        approval_id: UUID,
        actor_id: UUID,
        action: str,
        params_hash: str,
        session: Session,
    ) -> Approval:
        """在业务写**同一个 session** 内锁定、校验并消费审批。

        ``params_hash`` 由调用方用与创建 PENDING 审批时**同一套**参数指纹传入
        （即业务 Service 计算给 ``confirmations.consume`` 的 ``parameter_hash``），
        因此无需把原始参数搬进审批服务。调用方负责在消费后提交本事务
        （业务 mutation / 审计 / Outbox 同提交）。校验失败抛 ``ApprovalError``，
        由调用方回滚。
        """
        model = s_execute_get_for_update(session, approval_id)
        if model is None:
            raise ApprovalError("APPROVAL_NOT_FOUND", "审批记录不存在。", status_code=404)
        if model.actor_id != actor_id:
            raise ApprovalError("APPROVAL_ACTOR_MISMATCH", "操作人与确认人不一致，拒绝执行。")
        if model.action != action:
            raise ApprovalError("APPROVAL_ACTION_MISMATCH", "审批动作与待执行动作不一致。")
        if model.params_hash != params_hash:
            raise ApprovalError(
                "APPROVAL_PARAMS_CHANGED",
                "操作参数与确认时不一致，请重新确认。",
                status_code=409,
            )
        if model.status == ApprovalStatus.CONSUMED.value:
            # 同事务内重复消费等同幂等：直接返回已消费快照，不产生第二个副作用。
            return Approval.from_model(model)
        if model.status == ApprovalStatus.REJECTED.value:
            raise ApprovalError("APPROVAL_REJECTED", "该操作已被用户拒绝。")
        if model.status == ApprovalStatus.EXPIRED.value:
            raise ApprovalError("APPROVAL_EXPIRED", "确认已超时失效，请重新发起。")
        if model.status == ApprovalStatus.PENDING.value and _is_expired(model.expires_at):
            model.status = ApprovalStatus.EXPIRED.value
            raise ApprovalError("APPROVAL_EXPIRED", "确认已超时失效，请重新发起。")
        if model.status not in (ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value):
            raise ApprovalError(
                "APPROVAL_NOT_APPROVED",
                f"审批处于 {model.status} 状态，未获确认，拒绝执行。",
            )
        model.status = ApprovalStatus.CONSUMED.value
        model.consumed_at = datetime.now(timezone.utc)
        return Approval.from_model(model)


def s_execute_get_for_update(
    session: Session, approval_id: UUID
) -> AgentActionApprovalModel | None:
    """在给定 session 内以 ``FOR UPDATE`` 锁定审批行（供 consume 复用同一事务）。"""
    from sqlalchemy import select as _select

    return session.execute(
        _select(AgentActionApprovalModel)
        .where(AgentActionApprovalModel.id == approval_id)
        .with_for_update()
    ).scalar_one_or_none()


def _utcnow() -> datetime:
    """统一的 UTC 时间。SQLite 不存时区信息，存进去会是 naive datetime，
    但我们只需要与 ``datetime.now(UTC)`` 排序，所以 naive 情况下也用
    ``datetime.utcnow()`` 保持可比性。
    """
    return datetime.now(timezone.utc)


def _is_expired(stored: datetime) -> bool:
    """比较数据库里的 ``expires_at`` 是否已过（兼容 naive 与 aware datetime）。"""
    if stored.tzinfo is None:
        return stored < datetime.utcnow()
    return stored < _utcnow()
