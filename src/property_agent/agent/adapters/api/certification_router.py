"""Trusted PR7-B preproduction conversation preparation endpoint."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from property_agent.agent.adapters.api.dependencies import AgentRequestContext, get_agent_context
from property_agent.agent.runtime_version import AgentRuntimeVersion

router = APIRouter(prefix="/api/certification", tags=["certification"])


@router.post("/v2-conversations")
def prepare_v2_conversation(
    request: Request,
    context: AgentRequestContext = Depends(get_agent_context),  # noqa: B008
) -> dict[str, str]:
    """Create one server-owned persisted v2 conversation for an isolated load campaign."""
    conversation_id = f"pr7b-v2-{uuid4().hex}"
    snapshot = request.app.state.agent_conversations.start(
        conversation_id=conversation_id,
        context=context,
        current_house_id=context.current_house_id,
        runtime_version=AgentRuntimeVersion.V2.value,
    )
    return {
        "conversation_id": snapshot.conversation_id,
        "runtime_version": snapshot.runtime_version,
    }
