"""会话运行时 — 把主图、Conversation 业务表与恢复守卫串起来（PRD §6.5.8）。

一轮对话的完整链路：

    start   : Conversation.start → 注入可信身份 → graph.invoke → 同步业务表
    resume  : 恢复守卫三项校验   → graph.resume(state=校验后的快照) → 同步业务表

关键约束：

* ``thread_id`` 恒等于稳定的 ``conversation_id``；
* actor / community / house 只从可信上下文注入，用户自述一律忽略；
* resume 之前必须过恢复守卫，不允许直接调 ``graph.resume``；
* 中断挂起时会话进入 ``WAITING_CONFIRM``，转人工时进入 ``HANDOVER``。
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from property_agent.agent.application.conversation_service import (
    AgentContext,
    ConversationService,
    ConversationSnapshot,
)
from property_agent.agent.application.recovery import AgentRecoveryService
from property_agent.agent.graph_core import CompiledGraph
from property_agent.agent.state import GraphState


@dataclass(frozen=True)
class AgentTurn:
    """一轮执行结果。"""

    state: GraphState
    conversation: ConversationSnapshot
    interrupt: Any | None
    done: bool

    @property
    def awaiting_confirmation(self) -> bool:
        return not self.done and self.interrupt is not None

    @property
    def reply(self) -> str:
        for message in reversed(self.state.messages):
            if message.get("role") == "assistant":
                return str(message.get("content", ""))
        return ""


class AgentSessionRunner:
    def __init__(
        self,
        *,
        graph: CompiledGraph,
        conversations: ConversationService,
        recovery: AgentRecoveryService,
    ) -> None:
        self._graph = graph
        self._conversations = conversations
        self._recovery = recovery

    def start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
    ) -> AgentTurn:
        conversation = self._conversations.start(
            conversation_id=conversation_id,
            context=context,
            current_house_id=house_id,
        )
        state = GraphState(
            conversation_id=conversation_id,
            actor_id=context.actor_id,
            community_id=context.community_id,
            current_house_id=house_id or conversation.current_house_id,
            slots={"user_text": user_text, **(slots or {})},
        )
        state.add_message("user", user_text)
        result = self._graph.invoke(state, thread_id=conversation_id)
        return self._finalize(result)

    def resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
    ) -> AgentTurn:
        restored = self._recovery.restore(
            conversation_id, context, expected_action_hash=action_hash
        )
        result = self._graph.resume(
            conversation_id,
            {"confirmed": confirmed, "confirmation_token": confirmation_token},
            state=restored.state,
        )
        return self._finalize(result)

    def status(
        self, *, conversation_id: str, context: AgentContext
    ) -> tuple[ConversationSnapshot, dict[str, Any] | None]:
        """查询会话当前状态与待确认操作（只读，不触发闸门副作用）。"""
        conversation = self._conversations.require_owned_by(conversation_id, context)
        state = self._recovery.peek(conversation_id)
        pending = None
        if state is not None and state._interrupt_node is not None:
            pending = state.pending_action
        return conversation, pending

    def close(
        self, *, conversation_id: str, context: AgentContext
    ) -> ConversationSnapshot:
        self._conversations.require_owned_by(conversation_id, context)
        return self._conversations.close(conversation_id)

    # ---- 内部 ----

    def _finalize(self, result: dict[str, Any]) -> AgentTurn:
        state: GraphState = result["state"]
        done = bool(result["done"])
        conversation = self._conversations.sync_from_state(
            state, waiting_confirm=not done
        )
        return AgentTurn(
            state=state,
            conversation=conversation,
            interrupt=result.get("interrupt"),
            done=done,
        )
