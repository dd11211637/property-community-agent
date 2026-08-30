"""Typed public Agent, conversation, and Memory presentation contracts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from property_agent.platform.schemas import Envelope


class PresentationModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SlotOptionResponse(PresentationModel):
    label: str
    value: Any


class SlotPromptResponse(PresentationModel):
    field: str
    label: str
    prompt: str
    allow_custom: bool
    options: list[SlotOptionResponse]


class PendingConfirmationResponse(PresentationModel):
    summary: str
    tool: str | None
    params: dict[str, Any]
    action_hash: str | None
    issued_at: str | None


class AgentTurnResponse(PresentationModel):
    conversation_id: str
    status: str
    done: bool
    intent: str | None
    confidence: float | None
    operation_level: str | None
    reply: str
    messages: list[dict[str, Any]]
    facts: dict[str, Any] | None
    agent_trace: dict[str, Any] | list[dict[str, Any]] | None
    missing_slots: list[str]
    requested_slot: str | None
    slot_prompt: SlotPromptResponse | None
    handover_required: bool
    pending_confirmation: PendingConfirmationResponse | None
    error: str | None


class ConversationStatusResponse(PresentationModel):
    conversation_id: str
    status: str
    current_house_id: UUID | None
    last_intent: str | None
    handover_required: bool
    handover_ticket_id: UUID | None
    runtime_version: str
    pending_confirmation: PendingConfirmationResponse | None


class ConversationSummaryResponse(PresentationModel):
    conversation_id: str
    title: str
    status: str
    current_house_id: UUID | None
    last_intent: str | None
    last_message_at: str | None


class ConversationMessageResponse(PresentationModel):
    id: UUID
    role: Literal["user", "assistant", "system"]
    content: str
    intent: str | None
    house_id: UUID | None
    created_at: str


class MemoryResponse(PresentationModel):
    id: UUID
    memory_type: str
    content: str
    house_id: UUID | None
    source_conversation_id: str | None
    version: int
    expires_at: str | None
    created_at: str
    updated_at: str


class DeletedMemoryResponse(PresentationModel):
    id: UUID
    deleted: bool
    version: int


AgentTurnEnvelope = Envelope[AgentTurnResponse]
ConversationStatusEnvelope = Envelope[ConversationStatusResponse]
ConversationListEnvelope = Envelope[list[ConversationSummaryResponse]]
ConversationMessagesEnvelope = Envelope[list[ConversationMessageResponse]]
MemoryEnvelope = Envelope[MemoryResponse]
MemoryListEnvelope = Envelope[list[MemoryResponse]]
DeletedMemoryEnvelope = Envelope[DeletedMemoryResponse]
