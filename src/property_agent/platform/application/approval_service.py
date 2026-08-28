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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.infrastructure.approval_models import AgentActionApprovalModel

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


# 部分唯一索引：同一 (conversation, action, params_hash) 至多一个开放审批。
# create_pending 的并发竞争最终由它兜底，SAVEPOINT 仅负责优雅恢复，绝不替代它。
_OPEN_APPROVAL_UNIQUE_CONSTRAINT = "ux_agent_approval_open_action"


def _select_open_approval(
    session: Session,
    *,
    conversation_id: str,
    action: str,
    params_hash: str,
) -> AgentActionApprovalModel | None:
    """读取同一 (conversation, action, params_hash) 的开放审批（PENDING/APPROVED）。

    ``FOR UPDATE`` 只能锁定已存在行，无法消除"两线程都 SELECT 到 None 后并发
    INSERT"的竞争——该竞争由部分唯一索引 + SAVEPOINT 恢复在 ``create_pending``
    内兜底。本函数同时服务于 fast path 与 race 恢复后的 re-select。
    """
    return (
        session.execute(
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


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    """尽力从底层 DBAPI 异常取出违反的约束名（跨 psycopg3/2、SQLite）。

    psycopg3 把 ``constraint_name`` 直接挂在异常上；psycopg2 走 ``.diag``；
    SQLite 不暴露约束名。无法识别时返回 None，由调用方用 re-select 作最终仲裁，
    绝不静默吞掉非预期异常。
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    direct = getattr(orig, "constraint_name", None)
    if direct:
        return direct
    diag = getattr(orig, "diag", None)
    if diag is not None:
        return getattr(diag, "constraint_name", None)
    return None


def _is_open_approval_unique_race(exc: IntegrityError) -> bool:
    """是否为预期的开放审批并发重复竞争。

    * 约束名可识别且等于 ``ux_agent_approval_open_action`` → True（恢复）；
    * 约束名可识别但不符 → False（立即 re-raise，绝不吞掉其它约束冲突）；
    * 无法识别约束名 → True，交由后续 re-select 做最终判断（找不到同 open
      approval 即 re-raise）。
    """
    name = _integrity_constraint_name(exc)
    if name is None:
        return True
    return name == _OPEN_APPROVAL_UNIQUE_CONSTRAINT


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
        复用之（fast path）。否则在 SAVEPOINT 内 INSERT；若并发竞争中落败，部分唯一
        索引 ``ux_agent_approval_open_action`` 会拒绝第二条 INSERT，落败方在此
        **仅回滚 SAVEPOINT**（绝不回滚调用方传入 session 的整个事务），re-select 取
        回胜方已提交的同一条 open approval 并返回。胜方提交语义维持不变：

        * 内部新建 session → 本方法提交；
        * 调用方传入 session → 不替调用方提交。

        ``SELECT ... FOR UPDATE`` 无法锁定不存在的行，因此无法消除"两线程都读到
        None 后并发 INSERT"的竞争；correctness 来自 DB 唯一索引 + SAVEPOINT 恢复，
        而非单进程锁或 sleep/retry。
        """
        params_hash = canonical_hash(params)
        owned = session is not None
        s = session or self._session_factory()
        try:
            existing = _select_open_approval(
                s, conversation_id=conversation_id, action=action, params_hash=params_hash
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
            try:
                with s.begin_nested():
                    s.add(model)
                    s.flush()
            except IntegrityError as exc:
                # 仅恢复预期的开放审批并发重复；其它约束冲突必须原样抛出。
                if not _is_open_approval_unique_race(exc):
                    raise
                existing = _select_open_approval(
                    s,
                    conversation_id=conversation_id,
                    action=action,
                    params_hash=params_hash,
                )
                if existing is None:
                    # SAVEPOINT 已回滚，但本事务看不到并发胜方的 open approval
                    # （例如胜方仍在未提交状态）——无法安全恢复，必须抛出。
                    raise
                return Approval.from_model(existing)
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

    # ── 执行路径：APPROVED → CONSUMED（与业务 mutation 同事务） ──

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

        生命周期严格遵循 PENDING → APPROVED → CONSUMED：
        * 只有 APPROVED 状态的审批可以被消费（用户必须先点确认 approve）。
        * CONSUMED 视为幂等重放：同事务内重复消费不产生第二个副作用。
        * PENDING 状态的审批被拒绝（用户尚未确认），避免"签发即消费"反模式。

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
        if model.status == ApprovalStatus.PENDING.value:
            # PENDING 必须先经用户 approve；不允许跳过确认直接消费。
            if _is_expired(model.expires_at):
                model.status = ApprovalStatus.EXPIRED.value
                raise ApprovalError("APPROVAL_EXPIRED", "确认已超时失效，请重新发起。")
            raise ApprovalError(
                "APPROVAL_NOT_APPROVED",
                "审批尚未经用户确认，拒绝执行（必须先 approve）。",
                status_code=409,
            )
        if model.status != ApprovalStatus.APPROVED.value:
            raise ApprovalError(
                "APPROVAL_NOT_APPROVED",
                f"审批处于 {model.status} 状态，未获确认，拒绝执行。",
            )
        # APPROVED → CONSUMED
        if _is_expired(model.expires_at):
            model.status = ApprovalStatus.EXPIRED.value
            raise ApprovalError("APPROVAL_EXPIRED", "确认已超时失效，请重新发起。")
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
