"""Canonical persistence, pgvector retrieval, and conflict handling for Memory."""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_agent.agent.application.memory_reindex import MemoryReindexer
from property_agent.agent.application.memory_retention import governed_retention_days
from property_agent.agent.infrastructure.models import AgentMemoryModel
from property_agent.agent.memory_contracts import (
    EmbeddingProvider,
    MemoryCandidate,
    MemoryContext,
    MemoryKind,
    MemoryLifecycle,
    MemoryQuery,
    MemorySource,
    ReindexResult,
    RetrievedMemory,
)
from property_agent.platform.application.hashing import canonical_hash

logger = logging.getLogger(__name__)


class ScopedMemoryContext(Protocol):
    actor_id: UUID
    community_id: UUID
    house_ids: Collection[UUID]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def memory_data(row: AgentMemoryModel) -> dict:
    return {
        "id": str(row.id),
        "memory_type": row.memory_type,
        "memory_kind": row.memory_kind,
        "content": row.content,
        "house_id": str(row.house_id) if row.house_id else None,
        "source_conversation_id": row.source_conversation_id,
        "confirmed_by_user": row.confirmed_by_user,
        "lifecycle_status": row.lifecycle_status,
        "source_type": row.source_type,
        "provenance": row.provenance,
        "embedding_status": row.embedding_status,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


class MemoryRecordStore:
    """Own canonical Memory record queries; it has no business authority."""

    def __init__(self, session: Session, embedding_provider: EmbeddingProvider | None) -> None:
        self._session = session
        self._embedding_provider = embedding_provider

    def retrieve(self, query: MemoryQuery) -> MemoryContext:
        if (
            query.current_house_id is not None
            and query.current_house_id not in query.bound_house_ids
        ):
            return MemoryContext(degraded=True, degradation_reason="SCOPE_NOT_BOUND")
        limit = max(1, min(query.limit, 20))
        filters = self._retrieval_filters(query)
        structured = list(
            self._session.execute(
                select(AgentMemoryModel)
                .where(*filters)
                .order_by(AgentMemoryModel.updated_at.desc())
                .limit(limit * 3)
            ).scalars()
        )
        semantic, degraded_reason = self._semantic_candidates(query, filters, limit, structured)
        ranked = sorted(
            (self._retrieved(row, query, semantic.get(row.id)) for row in structured),
            key=lambda item: item.rank_score,
            reverse=True,
        )
        selected = self._within_bounds(ranked, limit, query.token_budget)
        return MemoryContext(
            selected,
            query_fingerprint=canonical_hash(
                {"text": query.text[:500], "house_id": str(query.current_house_id)}
            ),
            degraded=degraded_reason is not None,
            degradation_reason=degraded_reason,
        )

    def revalidate(self, query: MemoryQuery, previous: MemoryContext) -> MemoryContext:
        if not previous.items:
            return MemoryContext()
        references = {item.memory_id: item for item in previous.items}
        rows = self._session.execute(
            select(AgentMemoryModel).where(
                AgentMemoryModel.id.in_(references),
                AgentMemoryModel.actor_id == query.actor_id,
                AgentMemoryModel.community_id == query.community_id,
            )
        ).scalars()
        current = {row.id: row for row in rows}
        valid: list[RetrievedMemory] = []
        invalidated = False
        for item in previous.items:
            row = current.get(item.memory_id)
            if row is None or not self._same_effective_reference(row, item, query):
                invalidated = True
                continue
            valid.append(item)
        return MemoryContext(
            tuple(valid),
            previous.query_fingerprint,
            previous.degraded,
            previous.degradation_reason,
            basis_invalidated=invalidated,
            invalidation_reason="MEMORY_BASIS_CHANGED" if invalidated else None,
        )

    def persist_candidate(
        self,
        context: ScopedMemoryContext,
        *,
        candidate: MemoryCandidate,
        source_evidence_id: str,
        provenance: dict[str, object],
        house_id: UUID | None,
    ) -> dict:
        conflict_key = self._governed_conflict_key(candidate)
        candidate_id = self._candidate_id(candidate, conflict_key)
        existing = self._automatic_candidate(context, source_evidence_id, candidate_id)
        if existing is not None:
            return memory_data(existing)
        self._lock_conflict_scope(context, house_id, conflict_key or candidate_id)
        duplicate = self._exact_active(context, house_id, candidate_id)
        if duplicate is not None:
            sources = list((duplicate.provenance or {}).get("additional_sources") or [])
            if source_evidence_id not in sources:
                sources.append(source_evidence_id)
                duplicate.provenance = {
                    **dict(duplicate.provenance or {}),
                    "additional_sources": sources[-20:],
                }
                duplicate.version += 1
                duplicate.updated_at = now_utc()
                self._session.commit()
            return memory_data(duplicate)
        superseded = self._active_conflict(context, house_id, candidate, conflict_key)
        lifecycle = MemoryLifecycle.ACTIVE
        if (
            superseded is not None
            and superseded.confirmed_by_user
            and not candidate.confirmed_by_user
        ):
            lifecycle = MemoryLifecycle.CONFLICTED
            superseded = None
        elif superseded is not None:
            superseded.lifecycle_status = MemoryLifecycle.SUPERSEDED.value
            superseded.version += 1
            superseded.updated_at = now_utc()
        row = self._candidate_row(
            context,
            candidate,
            source_evidence_id,
            candidate_id,
            provenance,
            house_id,
            superseded,
            lifecycle,
            conflict_key,
        )
        self.index(row)
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self._automatic_candidate(context, source_evidence_id, candidate_id)
            if existing is None:
                raise
            return memory_data(existing)
        self._session.refresh(row)
        return memory_data(row)

    def index(self, row: AgentMemoryModel) -> None:
        if self._embedding_provider is None:
            row.embedding_status = "PENDING"
            return
        try:
            row.embedding = None
            result = self._embedding_provider.embed(row.content)
        except Exception:
            row.embedding_status = "FAILED"
            return
        row.embedding = list(result.vector)
        row.embedding_model = result.model
        row.embedding_version = result.version
        row.embedding_status = "READY"

    def reindex(self, *, limit: int = 100) -> ReindexResult:
        return MemoryReindexer(
            self._session,
            self._embedding_provider,
            self.index,
            clock=now_utc,
        ).run(limit=limit)

    def prepare_explicit_create(self, context: ScopedMemoryContext, row: AgentMemoryModel) -> None:
        """Serialize and supersede an existing governed explicit preference."""
        if not row.conflict_key:
            return
        self._lock_conflict_scope(context, row.house_id, row.conflict_key)
        old = self._session.execute(
            select(AgentMemoryModel)
            .where(
                AgentMemoryModel.actor_id == context.actor_id,
                AgentMemoryModel.community_id == context.community_id,
                AgentMemoryModel.house_id == row.house_id,
                AgentMemoryModel.conflict_key == row.conflict_key,
                AgentMemoryModel.lifecycle_status == MemoryLifecycle.ACTIVE.value,
                AgentMemoryModel.deleted_at.is_(None),
            )
            .order_by(AgentMemoryModel.updated_at.desc(), AgentMemoryModel.id.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if old is None:
            return
        old.lifecycle_status = MemoryLifecycle.SUPERSEDED.value
        old.version += 1
        old.updated_at = now_utc()
        row.supersedes_id = old.id

    def _semantic_candidates(self, query, filters, limit, structured):
        semantic: dict[UUID, float] = {}
        if self._embedding_provider is None:
            return semantic, "EMBEDDING_NOT_CONFIGURED"
        if self._session.bind is None:
            return semantic, "VECTOR_STORE_UNAVAILABLE"
        try:
            embedded = self._embedding_provider.embed(query.text)
            if self._session.bind.dialect.name != "postgresql":
                return semantic, None
            distance = AgentMemoryModel.embedding.cosine_distance(list(embedded.vector))
            rows = self._session.execute(
                select(AgentMemoryModel, distance.label("distance"))
                .where(*filters, AgentMemoryModel.embedding.is_not(None))
                .order_by(distance)
                .limit(limit * 3)
            )
            for row, value in rows:
                semantic[row.id] = max(0.0, 1.0 - float(value))
                if all(item.id != row.id for item in structured):
                    structured.append(row)
            return semantic, None
        except Exception:
            logger.exception("memory_vector_search_degraded")
            return semantic, "EMBEDDING_OR_VECTOR_UNAVAILABLE"

    @staticmethod
    def _within_bounds(ranked, limit: int, token_budget: int) -> tuple[RetrievedMemory, ...]:
        selected: list[RetrievedMemory] = []
        used_tokens = 0
        for item in ranked:
            cost = max(1, len(item.content) // 2)
            if used_tokens + cost > max(50, token_budget):
                continue
            selected.append(item)
            used_tokens += cost
            if len(selected) == limit:
                break
        return tuple(selected)

    @staticmethod
    def _candidate_id(candidate: MemoryCandidate, conflict_key: str | None) -> str:
        return canonical_hash(
            {
                "kind": candidate.kind.value,
                "type": candidate.memory_type,
                "content": " ".join(candidate.content.strip().lower().split()),
                "conflict_key": conflict_key,
            }
        )

    @staticmethod
    def _candidate_row(
        context,
        candidate,
        source_id,
        candidate_id,
        provenance,
        house_id,
        old,
        lifecycle,
        conflict_key,
    ):
        retention_days = governed_retention_days(candidate)
        expires_at = now_utc() + timedelta(days=retention_days) if retention_days else None
        return AgentMemoryModel(
            actor_id=context.actor_id,
            community_id=context.community_id,
            house_id=house_id,
            memory_type=candidate.memory_type,
            memory_kind=candidate.kind.value,
            content=candidate.content.strip(),
            canonical_key=conflict_key,
            source_type=candidate.source_type.value,
            source_conversation_id=str(provenance.get("conversation_id") or "") or None,
            source_evidence_id=source_id,
            candidate_id=candidate_id,
            provenance=provenance,
            confirmed_by_user=candidate.confirmed_by_user,
            confirmation_status=(
                "USER_CONFIRMED" if candidate.confirmed_by_user else "INFERRED_CANDIDATE"
            ),
            confidence=candidate.confidence,
            confidence_method=candidate.confidence_method,
            lifecycle_status=lifecycle.value,
            conflict_key=conflict_key,
            supersedes_id=old.id if old is not None else None,
            retention_class=("BOUNDED" if retention_days else "LONG_LIVED"),
            expires_at=expires_at,
        )

    @staticmethod
    def _same_effective_reference(row, item, query: MemoryQuery) -> bool:
        now = now_utc()
        scope_valid = row.house_id is None or (
            row.house_id == query.current_house_id and row.house_id in query.bound_house_ids
        )
        return bool(
            scope_valid
            and row.lifecycle_status == MemoryLifecycle.ACTIVE.value
            and row.deleted_at is None
            and (row.expires_at is None or as_utc(row.expires_at) > now)
            and row.version == item.record_version
            and memory_fingerprint(row) == item.content_fingerprint
        )

    def _retrieval_filters(self, query: MemoryQuery) -> tuple[object, ...]:
        house_filter = AgentMemoryModel.house_id.is_(None)
        if query.current_house_id is not None:
            house_filter = or_(house_filter, AgentMemoryModel.house_id == query.current_house_id)
        return (
            AgentMemoryModel.actor_id == query.actor_id,
            AgentMemoryModel.community_id == query.community_id,
            house_filter,
            AgentMemoryModel.memory_kind.in_(kind.value for kind in query.kinds),
            AgentMemoryModel.lifecycle_status == MemoryLifecycle.ACTIVE.value,
            AgentMemoryModel.deleted_at.is_(None),
            or_(AgentMemoryModel.expires_at.is_(None), AgentMemoryModel.expires_at > now_utc()),
        )

    @staticmethod
    def _retrieved(row, query, semantic_score) -> RetrievedMemory:
        age_days = max(0.0, (now_utc() - as_utc(row.updated_at)).total_seconds() / 86400)
        freshness = 1.0 / (1.0 + age_days / 30)
        scope = 0.15 if row.house_id == query.current_house_id and row.house_id else 0.0
        confirmation = 0.2 if row.confirmed_by_user else 0.05
        procedure_penalty = -0.1 if row.memory_kind == MemoryKind.PROCEDURAL_CANDIDATE else 0.0
        score = 0.45 * (semantic_score or 0.0) + 0.2 * freshness + scope + confirmation
        return RetrievedMemory(
            memory_id=row.id,
            kind=MemoryKind(row.memory_kind),
            memory_type=row.memory_type,
            content=row.content,
            house_id=row.house_id,
            source_type=MemorySource(row.source_type),
            source_evidence_id=row.source_evidence_id,
            provenance=dict(row.provenance or {}),
            confirmed_by_user=row.confirmed_by_user,
            confidence=row.confidence,
            lifecycle=MemoryLifecycle(row.lifecycle_status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            record_version=row.version,
            content_fingerprint=memory_fingerprint(row),
            semantic_score=semantic_score,
            rank_score=score + procedure_penalty,
        )

    def _automatic_candidate(self, context, source_id, candidate_id):
        return self._session.execute(
            select(AgentMemoryModel).where(
                AgentMemoryModel.actor_id == context.actor_id,
                AgentMemoryModel.community_id == context.community_id,
                AgentMemoryModel.source_evidence_id == source_id,
                AgentMemoryModel.candidate_id == candidate_id,
            )
        ).scalar_one_or_none()

    def _exact_active(self, context, house_id, candidate_id):
        return self._session.execute(
            select(AgentMemoryModel).where(
                AgentMemoryModel.actor_id == context.actor_id,
                AgentMemoryModel.community_id == context.community_id,
                AgentMemoryModel.house_id == house_id,
                AgentMemoryModel.candidate_id == candidate_id,
                AgentMemoryModel.lifecycle_status == MemoryLifecycle.ACTIVE.value,
                AgentMemoryModel.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    def _lock_conflict_scope(self, context, house_id, conflict_key) -> None:
        if not conflict_key or self._session.bind is None:
            return
        if self._session.bind.dialect.name == "postgresql":
            lock_key = f"{context.actor_id}:{context.community_id}:{house_id}:{conflict_key}"
            self._session.execute(
                select(sa_text("pg_advisory_xact_lock(hashtextextended(:key, 0))")).params(
                    key=lock_key
                )
            )

    def _active_conflict(self, context, house_id, candidate, conflict_key):
        if not candidate.correction or not conflict_key:
            return None
        return self._session.execute(
            select(AgentMemoryModel)
            .where(
                AgentMemoryModel.actor_id == context.actor_id,
                AgentMemoryModel.community_id == context.community_id,
                AgentMemoryModel.house_id == house_id,
                AgentMemoryModel.conflict_key == conflict_key,
                AgentMemoryModel.lifecycle_status == MemoryLifecycle.ACTIVE.value,
                AgentMemoryModel.deleted_at.is_(None),
            )
            .order_by(AgentMemoryModel.updated_at.desc(), AgentMemoryModel.id.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _governed_conflict_key(candidate: MemoryCandidate) -> str | None:
        if candidate.memory_type == "COMMUNICATION":
            return "communication-preference"
        if candidate.memory_type == "ACCESSIBILITY":
            return "accessibility-preference"
        if candidate.memory_type != "PREFERENCE" or not candidate.conflict_key:
            return None
        value = candidate.conflict_key.strip().lower()
        return value[:128] if value.replace("-", "").replace("_", "").isalnum() else None


def memory_fingerprint(row: AgentMemoryModel) -> str:
    return canonical_hash(
        {
            "id": str(row.id),
            "version": row.version,
            "content": row.content,
            "house_id": str(row.house_id) if row.house_id else None,
        }
    )
