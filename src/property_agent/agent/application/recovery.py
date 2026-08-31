"""待确认流程恢复 — PRD §6.5.8。

> 应用重启后，待确认流程必须能够恢复；恢复前必须重新检查用户会话、
> 房屋绑定和确认有效期。

三道闸按顺序执行，任何一道不过都**不允许** resume：

1. **用户会话** —— Conversation 业务表里的 actor / community 必须与本次请求的
   可信上下文一致，且会话未关闭；
2. **房屋绑定** —— 快照里的 ``current_house_id`` 必须仍在可信上下文的
   ``house_ids`` 里（绑定被撤销时快照会过时）；
3. **确认有效期** —— ``pending_action.issued_at`` 距今不得超过 TTL，
   与平台 ``ConfirmationService`` 的 5 分钟保持一致；
4. **参数指纹** —— 确认回执带回的 ``action_hash`` 必须与当前待确认的
   ``params_hash`` 一致，参数变化后旧确认作废。

校验失败会顺手作废这条待确认（清空 ``pending_action`` / 令牌 / 中断点），
避免过期或越权的写操作在后续某次请求里被"接着执行"。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from property_agent.agent.application.conversation_service import (
    AgentContext,
    ConversationService,
    ConversationSnapshot,
)
from property_agent.agent.application.errors import (
    AgentSessionError,
    AgentSessionErrorCode,
)
from property_agent.agent.state import GraphState

# 与 platform.application.confirmation_service.CONFIRMATION_TTL_MINUTES 对齐
DEFAULT_CONFIRMATION_TTL_SECONDS = 300

Clock = Callable[[], datetime]


class Checkpointer(Protocol):
    """Persistence contract required by guarded V2 recovery."""

    def load(self, thread_id: str) -> GraphState | None: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_issued_at(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RestoredSession:
    """通过全部校验、可以安全 resume 的会话。"""

    state: GraphState
    conversation: ConversationSnapshot
    interrupt_node: str | None
    pending_action: dict[str, Any] | None


class AgentRecoveryService:
    def __init__(
        self,
        *,
        conversations: ConversationService,
        checkpointer: Checkpointer,
        ttl_seconds: int = DEFAULT_CONFIRMATION_TTL_SECONDS,
        clock: Clock = _utcnow,
    ) -> None:
        self._conversations = conversations
        self._checkpointer = checkpointer
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    def restore(
        self,
        conversation_id: str,
        context: AgentContext,
        *,
        require_pending: bool = True,
        expected_action_hash: str | None = None,
    ) -> RestoredSession:
        # 闸 1：用户会话（所有权 + 生命周期）
        conversation = self._conversations.require_owned_by(conversation_id, context)

        state = self._checkpointer.load(conversation_id)
        if state is None:
            raise AgentSessionError(AgentSessionErrorCode.CHECKPOINT_NOT_FOUND)

        # 身份永远以可信上下文为准，绝不采信快照里的自述身份
        state.actor_id = context.actor_id
        state.community_id = context.community_id

        # 闸 2：房屋绑定
        house_id = state.current_house_id
        if house_id is not None and house_id not in set(context.house_ids):
            self.expire_pending(conversation_id)
            raise AgentSessionError(AgentSessionErrorCode.HOUSE_BINDING_REVOKED)

        pending = state.pending_action
        if require_pending and (pending is None or state._interrupt_node is None):
            raise AgentSessionError(AgentSessionErrorCode.NOTHING_PENDING)

        # 闸 3：确认有效期
        if pending is not None:
            issued_at = _parse_issued_at(pending.get("issued_at"))
            if issued_at is None or self._clock() - issued_at > self._ttl:
                self.expire_pending(conversation_id)
                raise AgentSessionError(AgentSessionErrorCode.CONFIRMATION_EXPIRED)

        # 闸 4：参数指纹（确认回执必须对应同一份参数）
        if pending is not None and expected_action_hash is not None:
            if expected_action_hash != pending.get("params_hash"):
                raise AgentSessionError(AgentSessionErrorCode.CONFIRMATION_PARAMS_CHANGED)

        return RestoredSession(
            state=state,
            conversation=conversation,
            interrupt_node=state._interrupt_node,
            pending_action=pending,
        )

    def peek(self, conversation_id: str) -> GraphState | None:
        """只读取快照，不做任何闸门校验（供状态查询用）。"""
        return self._checkpointer.load(conversation_id)

    def expire_pending(self, conversation_id: str) -> None:
        """作废这条待确认：清空待办 / 令牌 / 中断点，会话回到 ACTIVE。"""
        state = self._checkpointer.load(conversation_id)
        if state is None:
            return
        state.pending_action = None
        state.confirmation_token = None
        state.operation_level = None
        state._resume = None
        state._interrupt_node = None
        self._checkpointer.save(conversation_id, state)
        if self._conversations.get(conversation_id) is not None:
            self._conversations.sync_from_state(state, waiting_confirm=False)

    def pending_conversations(self) -> list[str]:
        """重启后可用于巡检"还有哪些会话停在确认上"。"""
        pending = getattr(self._checkpointer, "pending_threads", None)
        if callable(pending):
            return list(pending())
        return [
            thread_id
            for thread_id in self._checkpointer.list_threads()
            if (snapshot := self._checkpointer.load(thread_id)) is not None
            and snapshot.pending_action is not None
            and snapshot._interrupt_node is not None
        ]
