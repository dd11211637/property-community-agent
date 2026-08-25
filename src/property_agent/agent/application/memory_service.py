"""Unified application owner for transcripts and governed long-term memory."""

from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from property_agent.agent.application.memory_store import MemoryRecordStore, memory_data
from property_agent.agent.infrastructure.models import (
    AgentMemoryModel,
    AgentMessageModel,
    ConversationModel,
)
from property_agent.agent.memory_contracts import (
    EmbeddingProvider,
    MemoryCandidate,
    MemoryKind,
    MemoryLifecycle,
    MemoryQuery,
    MemorySource,
)
from property_agent.agent.memory_contracts import (
    MemoryContext as RetrievedMemoryContext,
)
from property_agent.platform.errors import BusinessError

MEMORY_TYPES = frozenset({"PREFERENCE", "COMMUNICATION", "ACCESSIBILITY", "SERVICE_NOTE"})
MESSAGE_ROLES = frozenset({"user", "assistant", "system"})
_memory_data = memory_data


class MemoryContext(Protocol):
    actor_id: UUID
    community_id: UUID
    house_ids: Collection[UUID]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentMemoryService:
    """One governed API for CRUD, retrieval, and accepted-evidence writes."""

    def __init__(
        self, session: Session, *, embedding_provider: EmbeddingProvider | None = None
    ) -> None:
        self._session = session
        self._store = MemoryRecordStore(session, embedding_provider)

    def record_turn(
        self,
        *,
        conversation_id: str,
        context: MemoryContext,
        user_text: str,
        assistant_text: str,
        house_id: UUID | None,
        intent: str | None,
    ) -> None:
        conversation = self._owned_conversation(conversation_id, context)
        timestamp = _now()
        self._session.add_all(
            [
                AgentMessageModel(
                    conversation_id=conversation_id,
                    actor_id=context.actor_id,
                    community_id=context.community_id,
                    house_id=house_id,
                    role="user",
                    content=user_text,
                    intent=intent,
                    created_at=timestamp,
                ),
                AgentMessageModel(
                    conversation_id=conversation_id,
                    actor_id=context.actor_id,
                    community_id=context.community_id,
                    house_id=house_id,
                    role="assistant",
                    content=assistant_text or "本轮操作已处理。",
                    intent=intent,
                    created_at=timestamp + timedelta(microseconds=1),
                ),
            ]
        )
        if not conversation.title or conversation.title == "新对话":
            conversation.title = user_text.strip()[:120]
        conversation.last_message_at = timestamp
        self._session.commit()

    def list_conversations(self, context: MemoryContext, *, limit: int = 50) -> list[dict]:
        rows = self._session.execute(
            select(ConversationModel)
            .where(
                ConversationModel.actor_id == context.actor_id,
                ConversationModel.community_id == context.community_id,
            )
            .order_by(
                ConversationModel.last_message_at.desc().nullslast(),
                ConversationModel.updated_at.desc(),
            )
            .limit(limit)
        ).scalars()
        return [
            {
                "conversation_id": row.conversation_id,
                "title": row.title or "新对话",
                "status": row.status,
                "current_house_id": str(row.current_house_id) if row.current_house_id else None,
                "last_intent": row.last_intent,
                "last_message_at": (
                    row.last_message_at.isoformat() if row.last_message_at else None
                ),
            }
            for row in rows
        ]

    def list_messages(self, conversation_id: str, context: MemoryContext) -> list[dict]:
        self._owned_conversation(conversation_id, context)
        rows = self._session.execute(
            select(AgentMessageModel)
            .where(AgentMessageModel.conversation_id == conversation_id)
            .order_by(AgentMessageModel.created_at, AgentMessageModel.id)
        ).scalars()
        return [
            {
                "id": str(row.id),
                "role": row.role,
                "content": row.content,
                "intent": row.intent,
                "house_id": str(row.house_id) if row.house_id else None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    def list_memories(self, context: MemoryContext) -> list[dict]:
        rows = self._session.execute(
            select(AgentMemoryModel)
            .where(
                AgentMemoryModel.actor_id == context.actor_id,
                AgentMemoryModel.community_id == context.community_id,
                AgentMemoryModel.deleted_at.is_(None),
                AgentMemoryModel.lifecycle_status == MemoryLifecycle.ACTIVE.value,
            )
            .order_by(AgentMemoryModel.updated_at.desc())
        ).scalars()
        return [memory_data(row) for row in rows if not row.expires_at or row.expires_at > _now()]

    def create_memory(
        self,
        context: MemoryContext,
        *,
        memory_type: str,
        content: str,
        house_id: UUID | None = None,
        source_conversation_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict:
        self._validate_memory(memory_type, content, house_id, context)
        if source_conversation_id:
            self._owned_conversation(source_conversation_id, context)
        row = AgentMemoryModel(
            actor_id=context.actor_id,
            community_id=context.community_id,
            house_id=house_id,
            memory_type=memory_type,
            memory_kind=MemoryKind.SEMANTIC.value,
            content=content.strip(),
            canonical_key=("communication-preference" if memory_type == "COMMUNICATION" else None),
            source_conversation_id=source_conversation_id,
            confirmed_by_user=True,
            source_type=MemorySource.MEMORY_API.value,
            provenance={"source": "authenticated_memory_api"},
            confirmation_status="USER_CONFIRMED",
            conflict_key=("communication-preference" if memory_type == "COMMUNICATION" else None),
            expires_at=expires_at,
        )
        self._store.prepare_explicit_create(context, row)
        self._store.index(row)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return memory_data(row)

    def update_memory(
        self,
        memory_id: UUID,
        context: MemoryContext,
        *,
        content: str,
        expected_version: int,
    ) -> dict:
        row = self._owned_memory(memory_id, context)
        self._validate_memory(row.memory_type, content, row.house_id, context)
        new_version = self._apply_versioned_update(
            memory_id,
            context,
            expected_version,
            content=content.strip(),
            embedding=None,
            embedding_status="PENDING",
        )
        if new_version is None:
            raise BusinessError("VERSION_CONFLICT", "Memory was modified by another request.", 409)
        self._session.commit()
        self._session.refresh(row)
        self._store.index(row)
        self._session.commit()
        self._session.refresh(row)
        return memory_data(row)

    def delete_memory(
        self, memory_id: UUID, context: MemoryContext, *, expected_version: int
    ) -> dict:
        row = self._owned_memory(memory_id, context)
        new_version = self._apply_versioned_update(
            memory_id,
            context,
            expected_version,
            deleted_at=_now(),
            lifecycle_status=MemoryLifecycle.DELETED.value,
            embedding=None,
            embedding_status="DELETED",
            cleanup_status="COMPLETED",
        )
        if new_version is None:
            raise BusinessError("VERSION_CONFLICT", "Memory was modified by another request.", 409)
        self._session.commit()
        return {"id": str(row.id), "deleted": True, "version": new_version}

    def retrieve(self, query: MemoryQuery) -> RetrievedMemoryContext:
        return self._store.retrieve(query)

    def revalidate(self, query: MemoryQuery, memory_ids: set[UUID]) -> RetrievedMemoryContext:
        return self._store.revalidate(query, memory_ids)

    def persist_candidate(
        self,
        context: MemoryContext,
        *,
        candidate: MemoryCandidate,
        source_evidence_id: str,
        provenance: dict[str, object],
        house_id: UUID | None,
    ) -> dict:
        self._validate_memory(candidate.memory_type, candidate.content, house_id, context)
        return self._store.persist_candidate(
            context,
            candidate=candidate,
            source_evidence_id=source_evidence_id,
            provenance=provenance,
            house_id=house_id,
        )

    def _apply_versioned_update(
        self,
        memory_id: UUID,
        context: MemoryContext,
        expected_version: int,
        **values: object,
    ) -> int | None:
        result = self._session.execute(
            update(AgentMemoryModel)
            .where(
                AgentMemoryModel.id == memory_id,
                AgentMemoryModel.actor_id == context.actor_id,
                AgentMemoryModel.community_id == context.community_id,
                AgentMemoryModel.deleted_at.is_(None),
                AgentMemoryModel.version == expected_version,
            )
            .values(version=AgentMemoryModel.version + 1, updated_at=_now(), **values)
            .returning(AgentMemoryModel.version)
        )
        return result.scalar_one_or_none()

    def _owned_conversation(
        self, conversation_id: str, context: MemoryContext
    ) -> ConversationModel:
        row = self._session.execute(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id,
                ConversationModel.actor_id == context.actor_id,
                ConversationModel.community_id == context.community_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise BusinessError("CONVERSATION_NOT_FOUND", "Conversation was not found.", 404)
        return row

    def _owned_memory(self, memory_id: UUID, context: MemoryContext) -> AgentMemoryModel:
        row = self._session.execute(
            select(AgentMemoryModel).where(
                AgentMemoryModel.id == memory_id,
                AgentMemoryModel.actor_id == context.actor_id,
                AgentMemoryModel.community_id == context.community_id,
                AgentMemoryModel.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise BusinessError("MEMORY_NOT_FOUND", "Memory was not found.", 404)
        return row

    @staticmethod
    def _validate_memory(
        memory_type: str,
        content: str,
        house_id: UUID | None,
        context: MemoryContext,
    ) -> None:
        if memory_type not in MEMORY_TYPES:
            raise BusinessError("INVALID_MEMORY_TYPE", "Unsupported memory type.", 422)
        if not content.strip() or len(content.strip()) > 500:
            raise BusinessError("INVALID_MEMORY", "Memory must contain 1 to 500 characters.", 422)
        if house_id is not None and house_id not in context.house_ids:
            raise BusinessError("HOUSE_NOT_BOUND", "The house is not bound to this account.", 403)
