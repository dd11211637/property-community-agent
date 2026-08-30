"""AgentRuntimeFacade — API 唯一依赖的运行时门面（PR4 §10 / §11 第一层）。

两层拆分：

* 本门面只暴露 ``start / stream_start / resume / stream_resume / status / close``，
  供 API 层依赖；**绝不**依赖具体 ``AgentSessionRunner`` 的 isinstance，也不依赖
  任何 GraphEngine 类型（§10）。
* 生命周期（lease / 心跳 / fence / checkpoint CAS / 恢复 / 确认准备 / conversation
  同步 / 关闭竞争 / transcript / 观测）全部委托给 ``TurnLifecycle``（即
  ``AgentSessionRunner``，单一 P0 正确性拥有者）。
* 图执行委托给按会话钉死版本选出的 ``GraphEngine``：v1 = ``LegacyGraphEngine``
  （runner 内部包裹自定义 CompiledGraph）；v2 = ``LangGraphEngine``。

版本选择：新会话由服务端 ``RuntimeSelectionPolicy.select_new()`` 钉死；恢复/查询
服从持久化版本，绝不切换（§8 / §9）。生产装配不注入 pilot 策略，公网 v2 硬 0%。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from property_agent.agent.application.conversation_service import (
    AgentContext,
    ConversationService,
    ConversationSnapshot,
)
from property_agent.agent.application.errors import AgentSessionError, AgentSessionErrorCode
from property_agent.agent.application.graph_engine import GraphEngine
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.runtime_version import (
    AgentRuntimeVersion,
    RequiredRuntimeUnavailable,
    RuntimeSelectionPolicy,
)


@runtime_checkable
class AgentRuntimeFacade(Protocol):
    """API 层依赖的运行时契约（与具体实现解耦）。"""

    def start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
    ) -> Any: ...

    def stream_start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
    ) -> Any: ...

    def resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
    ) -> Any: ...

    def stream_resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
    ) -> Any: ...

    def status(
        self, *, conversation_id: str, context: AgentContext
    ) -> tuple[ConversationSnapshot, dict[str, Any] | None]: ...

    def close(self, *, conversation_id: str, context: AgentContext) -> ConversationSnapshot: ...


class AgentRuntimeFacadeImpl:
    """``AgentRuntimeFacade`` 的具体实现（§10 第一层）。"""

    def __init__(
        self,
        *,
        lifecycle: AgentSessionRunner,
        conversations: ConversationService,
        policy: RuntimeSelectionPolicy,
        v2_engine: GraphEngine | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._conversations = conversations
        self._policy = policy
        self._v2_engine = v2_engine

    # ---- 版本选择（PR4 §8 / §9）----

    def _selection_for_start(
        self,
        existing: ConversationSnapshot | None,
        context: AgentContext,
        conversation_id: str,
    ) -> tuple[GraphEngine | None, str]:
        try:
            version = (
                self._policy.select_for(existing.runtime_version)
                if existing is not None
                else self._policy.select_new(
                    community_id=context.community_id,
                    actor_id=context.actor_id,
                    conversation_id=conversation_id,
                )
            )
        except RequiredRuntimeUnavailable as exc:
            raise AgentSessionError(
                AgentSessionErrorCode.RUNTIME_UNAVAILABLE,
                f"Agent V2 运行时尚未就绪（{exc.reason.value}）。",
            ) from exc
        if version == AgentRuntimeVersion.V2 and self._v2_engine is None:
            raise RuntimeError("pinned v2 runtime is unavailable")
        return (self._v2_engine if version == AgentRuntimeVersion.V2 else None, version.value)

    def _engine_for_existing(self, snapshot: ConversationSnapshot) -> GraphEngine | None:
        # 已有会话：严格服从持久化版本，绝不切换。
        if snapshot.is_v2 and self._v2_engine is None:
            raise RuntimeError("pinned v2 runtime is unavailable")
        return self._v2_engine if snapshot.is_v2 else None

    # ---- 公共 API（与 runner 同签名，便于 router 复用）----

    def start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
    ):
        return self._lifecycle.start(
            conversation_id=conversation_id,
            context=context,
            user_text=user_text,
            house_id=house_id,
            slots=slots,
            runtime_route=self._selection_for_start,
        )

    def stream_start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
    ):
        return self._lifecycle.stream_start(
            conversation_id=conversation_id,
            context=context,
            user_text=user_text,
            house_id=house_id,
            slots=slots,
            runtime_route=self._selection_for_start,
        )

    def resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
    ):
        snapshot = self._conversations.require_owned_by(conversation_id, context)
        engine = self._engine_for_existing(snapshot)
        return self._lifecycle.resume(
            conversation_id=conversation_id,
            context=context,
            confirmed=confirmed,
            confirmation_token=confirmation_token,
            action_hash=action_hash,
            engine=engine,
            runtime_version=snapshot.runtime_version,
        )

    def stream_resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
    ):
        snapshot = self._conversations.require_owned_by(conversation_id, context)
        engine = self._engine_for_existing(snapshot)
        return self._lifecycle.stream_resume(
            conversation_id=conversation_id,
            context=context,
            confirmed=confirmed,
            confirmation_token=confirmation_token,
            action_hash=action_hash,
            engine=engine,
            runtime_version=snapshot.runtime_version,
        )

    def status(
        self, *, conversation_id: str, context: AgentContext
    ) -> tuple[ConversationSnapshot, dict[str, Any] | None]:
        return self._lifecycle.status(conversation_id=conversation_id, context=context)

    def close(self, *, conversation_id: str, context: AgentContext) -> ConversationSnapshot:
        return self._lifecycle.close(conversation_id=conversation_id, context=context)
