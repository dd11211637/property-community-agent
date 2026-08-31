"""Stable API facade over the V2-only Agent turn lifecycle."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from property_agent.agent.application.conversation_service import (
    AgentContext,
    ConversationSnapshot,
)
from property_agent.agent.application.runner import AgentSessionRunner


@runtime_checkable
class AgentRuntimeFacade(Protocol):
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
    """Preserve the public facade while delegating to one V2 lifecycle."""

    def __init__(self, *, lifecycle: AgentSessionRunner) -> None:
        self._lifecycle = lifecycle

    def start(self, **kwargs):
        return self._lifecycle.start(**kwargs)

    def stream_start(self, **kwargs):
        return self._lifecycle.stream_start(**kwargs)

    def resume(self, **kwargs):
        return self._lifecycle.resume(**kwargs)

    def stream_resume(self, **kwargs):
        return self._lifecycle.stream_resume(**kwargs)

    def status(self, **kwargs):
        return self._lifecycle.status(**kwargs)

    def close(self, **kwargs):
        return self._lifecycle.close(**kwargs)
