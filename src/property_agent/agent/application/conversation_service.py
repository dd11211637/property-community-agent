"""Conversation 业务表服务 — PRD §6.5.8。

Conversation 表保存的是**业务事实**：会话归谁所有、当前在哪套房屋、
是否已转人工、处在什么生命周期。它与 Checkpointer 职责不同：

* Checkpointer 丢了，最多是"这一轮要重来"；
* Conversation 丢了，等于会话所有权和接管状态无从追溯。

因此所有权校验只认 Conversation 表 + 可信 RequestContext，
**绝不**采信 Checkpointer 快照里的身份字段。
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from property_agent.agent.application.errors import (
    AgentSessionError,
    AgentSessionErrorCode,
)
from property_agent.agent.infrastructure.models import ConversationModel
from property_agent.agent.state import GraphState

SessionFactory = Callable[[], Session]


class ConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WAITING_CONFIRM = "WAITING_CONFIRM"
    HANDOVER = "HANDOVER"
    CLOSED = "CLOSED"


class AgentContext(Protocol):
    """API 层注入的可信请求上下文（只读身份来源）。"""

    @property
    def actor_id(self) -> UUID: ...

    @property
    def community_id(self) -> UUID: ...

    @property
    def house_ids(self) -> Collection[UUID]: ...


@dataclass(frozen=True)
class ConversationSnapshot:
    conversation_id: str
    actor_id: UUID
    community_id: UUID
    current_house_id: UUID | None
    status: str
    handover_required: bool
    last_intent: str | None
    handover_ticket_id: UUID | None = None

    @property
    def is_closed(self) -> bool:
        return self.status == ConversationStatus.CLOSED.value


def _to_snapshot(row: ConversationModel) -> ConversationSnapshot:
    return ConversationSnapshot(
        conversation_id=row.conversation_id,
        actor_id=row.actor_id,
        community_id=row.community_id,
        current_house_id=row.current_house_id,
        status=row.status,
        handover_required=row.handover_required,
        last_intent=row.last_intent,
        handover_ticket_id=row.handover_ticket_id,
    )


class ConversationService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # ---- 查询 ----

    def get(self, conversation_id: str) -> ConversationSnapshot | None:
        session = self._session_factory()
        try:
            row = self._find(session, conversation_id)
            return _to_snapshot(row) if row is not None else None
        finally:
            session.close()

    def require_owned_by(self, conversation_id: str, context: AgentContext) -> ConversationSnapshot:
        """会话所有权 + 生命周期校验（恢复前第一道闸）。"""
        snapshot = self.get(conversation_id)
        if snapshot is None:
            raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_NOT_FOUND)
        if snapshot.actor_id != context.actor_id or snapshot.community_id != context.community_id:
            raise AgentSessionError(AgentSessionErrorCode.SESSION_MISMATCH)
        if snapshot.is_closed:
            raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_CLOSED)
        return snapshot

    # ---- 写入 ----

    def start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        current_house_id: UUID | None = None,
    ) -> ConversationSnapshot:
        """开启或复用会话（幂等）。归属他人的 conversation_id 一律拒绝。"""
        session = self._session_factory()
        try:
            row = self._find(session, conversation_id)
            if row is None:
                row = ConversationModel(
                    conversation_id=conversation_id,
                    actor_id=context.actor_id,
                    community_id=context.community_id,
                    current_house_id=current_house_id,
                    status=ConversationStatus.ACTIVE.value,
                    handover_required=False,
                )
                session.add(row)
            else:
                if row.actor_id != context.actor_id or row.community_id != context.community_id:
                    raise AgentSessionError(AgentSessionErrorCode.SESSION_MISMATCH)
                if row.status == ConversationStatus.CLOSED.value:
                    raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_CLOSED)
                if current_house_id is not None:
                    row.current_house_id = current_house_id
            session.commit()
            session.refresh(row)
            return _to_snapshot(row)
        finally:
            session.close()

    def sync_from_state(self, state: GraphState, *, waiting_confirm: bool) -> ConversationSnapshot:
        """把一轮执行结果同步回业务表：当前房屋 / 接管状态 / 生命周期。

        P0-7: CLOSED conversation 不允许被旧 run 恢复为 ACTIVE / WAITING_CONFIRM /
        HANDOVER。使用**原子条件 UPDATE**（``status <> 'CLOSED'``），0 行返回即
        ``CONVERSATION_CLOSED``——消除原先「读后判断再更新」的 TOCTOU 窗口：即使
        close() 在读取之后、更新之前发生，原子 UPDATE 也会拦截复活，旧 turn 的写
        被拒绝，杜绝 "已关闭会话被复活"。
        """

        session = self._session_factory()
        try:
            row = self._find(session, state.conversation_id)
            if row is None:
                raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_NOT_FOUND)
            new_status = self._status_for(state, waiting_confirm)
            if row.status == ConversationStatus.CLOSED.value:
                # 快速路径：读到已是 CLOSED，直接拒绝（原子 UPDATE 仍作为兜底保证）。
                raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_CLOSED)
            # 原子条件更新：仅更新非 CLOSED 会话，0 行即被并发 close 抢先，不复活。
            # 注意：current_house_id 为 UUID，原始 SQL 绑定在 SQLite 下需先转 str
            # （PostgreSQL/psycopg 亦可接受字符串形式的 UUID）。
            current_house_id = (
                str(state.current_house_id) if state.current_house_id is not None else None
            )
            result = session.execute(
                text(
                    """
                    UPDATE agent_conversations
                    SET current_house_id = :current_house_id,
                        last_intent = :last_intent,
                        handover_required = :handover_required,
                        status = :status
                    WHERE conversation_id = :conversation_id AND status <> 'CLOSED'
                    RETURNING conversation_id
                    """
                ),
                {
                    "current_house_id": current_house_id,
                    "last_intent": state.intent,
                    "handover_required": bool(state.handover_required),
                    "status": new_status,
                    "conversation_id": state.conversation_id,
                },
            ).fetchone()
            if result is None:
                # 0 行：并发 close 已在读取后将会话置为 CLOSED，原子 UPDATE 拦截了
                # 复活，拒绝旧 turn 的写。
                raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_CLOSED)
            session.commit()
            # 原始 SQL UPDATE 不更新内存中的 ORM 对象；expire_on_commit=False 下
            # 需显式 refresh 才能拿到原子更新后的状态。
            session.refresh(row)
            return _to_snapshot(row)
        finally:
            session.close()

    @staticmethod
    def _status_for(state: GraphState, waiting_confirm: bool) -> str:
        if state.handover_required:
            return ConversationStatus.HANDOVER.value
        if waiting_confirm:
            return ConversationStatus.WAITING_CONFIRM.value
        return ConversationStatus.ACTIVE.value

    def mark_handover(
        self, conversation_id: str, *, ticket_id: UUID | None = None
    ) -> ConversationSnapshot:
        session = self._session_factory()
        try:
            row = self._find(session, conversation_id)
            if row is None:
                raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_NOT_FOUND)
            row.handover_required = True
            row.status = ConversationStatus.HANDOVER.value
            if ticket_id is not None:
                row.handover_ticket_id = ticket_id
            session.commit()
            session.refresh(row)
            return _to_snapshot(row)
        finally:
            session.close()

    def close(self, conversation_id: str) -> ConversationSnapshot:
        session = self._session_factory()
        try:
            row = self._find(session, conversation_id)
            if row is None:
                raise AgentSessionError(AgentSessionErrorCode.CONVERSATION_NOT_FOUND)
            # 原子条件关闭：仅当未关闭时置 CLOSED；重复 close 幂等（0 行也无妨）。
            session.execute(
                text(
                    """
                    UPDATE agent_conversations
                    SET status = 'CLOSED', closed_at = :closed_at
                    WHERE conversation_id = :conversation_id AND status <> 'CLOSED'
                    """
                ),
                {
                    "closed_at": datetime.now(timezone.utc),
                    "conversation_id": conversation_id,
                },
            )
            session.commit()
            # 原始 SQL UPDATE 不更新内存中的 ORM 对象；本服务 session_factory 使用
            # expire_on_commit=False，需显式 refresh 才能拿到原子更新后的状态。
            session.refresh(row)
            return _to_snapshot(row)
        finally:
            session.close()

    # ---- 内部 ----

    @staticmethod
    def _find(session: Session, conversation_id: str) -> ConversationModel | None:
        return session.execute(
            select(ConversationModel).where(ConversationModel.conversation_id == conversation_id)
        ).scalar_one_or_none()
