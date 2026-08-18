"""User-visible Agent history and memory management endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from property_agent.agent.adapters.api.dependencies import AgentRequestContext, get_agent_context
from property_agent.agent.adapters.api.memory_schemas import (
    CreateMemoryRequest,
    DeleteMemoryRequest,
    UpdateMemoryRequest,
)
from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.schemas import Envelope

router = APIRouter(prefix="/api/agent", tags=["agent-memory"])
ContextDep = Annotated[AgentRequestContext, Depends(get_agent_context)]
DbDep = Annotated[Session, Depends(get_db)]


def _envelope(data: object, context: AgentRequestContext) -> Envelope:
    return Envelope(success=True, data=data, error=None, request_id=context.request_id)


@router.get("/conversations", response_model=Envelope)
def list_conversations(
    context: ContextDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Envelope:
    return _envelope(AgentMemoryService(db).list_conversations(context, limit=limit), context)


@router.get("/conversations/{conversation_id}/messages", response_model=Envelope)
def list_messages(conversation_id: str, context: ContextDep, db: DbDep) -> Envelope:
    return _envelope(AgentMemoryService(db).list_messages(conversation_id, context), context)


@router.get("/memories", response_model=Envelope)
def list_memories(context: ContextDep, db: DbDep) -> Envelope:
    return _envelope(AgentMemoryService(db).list_memories(context), context)


@router.post("/memories", response_model=Envelope)
def create_memory(
    payload: CreateMemoryRequest,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    data = AgentMemoryService(db).create_memory(
        context,
        memory_type=payload.memory_type,
        content=payload.content,
        house_id=payload.house_id,
        source_conversation_id=payload.source_conversation_id,
        expires_at=payload.expires_at,
    )
    return _envelope(data, context)


@router.patch("/memories/{memory_id}", response_model=Envelope)
def update_memory(
    memory_id: UUID,
    payload: UpdateMemoryRequest,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    data = AgentMemoryService(db).update_memory(
        memory_id,
        context,
        content=payload.content,
        expected_version=payload.expected_version,
    )
    return _envelope(data, context)


@router.delete("/memories/{memory_id}", response_model=Envelope)
def delete_memory(
    memory_id: UUID,
    payload: DeleteMemoryRequest,
    context: ContextDep,
    db: DbDep,
) -> Envelope:
    data = AgentMemoryService(db).delete_memory(
        memory_id,
        context,
        expected_version=payload.expected_version,
    )
    return _envelope(data, context)
