"""Typed, bounded contracts for untrusted long-term memory reasoning context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class MemoryKind(StrEnum):
    SEMANTIC = "SEMANTIC"
    EPISODIC = "EPISODIC"
    PROCEDURAL_CANDIDATE = "PROCEDURAL_CANDIDATE"


class MemoryLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CONFLICTED = "CONFLICTED"
    DELETED = "DELETED"


class MemorySource(StrEnum):
    MEMORY_API = "MEMORY_API"
    EXPLICIT_STATEMENT = "EXPLICIT_STATEMENT"
    USER_CORRECTION = "USER_CORRECTION"
    COMPLETED_PLAN = "COMPLETED_PLAN"
    HUMAN_SERVICE_NOTE = "HUMAN_SERVICE_NOTE"


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    memory_id: UUID
    kind: MemoryKind
    memory_type: str
    content: str
    house_id: UUID | None
    source_type: MemorySource
    source_evidence_id: str | None
    provenance: dict[str, object]
    confirmed_by_user: bool
    confidence: float | None
    lifecycle: MemoryLifecycle
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    semantic_score: float | None = None
    rank_score: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": str(self.memory_id),
            "kind": self.kind.value,
            "memory_type": self.memory_type,
            "content": self.content,
            "house_id": str(self.house_id) if self.house_id else None,
            "source_type": self.source_type.value,
            "source_evidence_id": self.source_evidence_id,
            "provenance": self.provenance,
            "confirmed_by_user": self.confirmed_by_user,
            "confidence": self.confidence,
            "lifecycle": self.lifecycle.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "semantic_score": self.semantic_score,
            "rank_score": self.rank_score,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RetrievedMemory:
        return cls(
            memory_id=UUID(str(value["memory_id"])),
            kind=MemoryKind(str(value["kind"])),
            memory_type=str(value["memory_type"]),
            content=str(value["content"]),
            house_id=UUID(str(value["house_id"])) if value.get("house_id") else None,
            source_type=MemorySource(str(value["source_type"])),
            source_evidence_id=(
                str(value["source_evidence_id"]) if value.get("source_evidence_id") else None
            ),
            provenance=dict(value.get("provenance") or {}),
            confirmed_by_user=bool(value.get("confirmed_by_user")),
            confidence=float(value["confidence"]) if value.get("confidence") is not None else None,
            lifecycle=MemoryLifecycle(str(value["lifecycle"])),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
            expires_at=(
                datetime.fromisoformat(str(value["expires_at"]))
                if value.get("expires_at")
                else None
            ),
            semantic_score=(
                float(value["semantic_score"]) if value.get("semantic_score") is not None else None
            ),
            rank_score=float(value.get("rank_score") or 0.0),
        )


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    text: str
    actor_id: UUID
    community_id: UUID
    current_house_id: UUID | None
    bound_house_ids: frozenset[UUID]
    kinds: frozenset[MemoryKind] = field(default_factory=lambda: frozenset(MemoryKind))
    limit: int = 8
    token_budget: int = 500


@dataclass(frozen=True, slots=True)
class MemoryContext:
    items: tuple[RetrievedMemory, ...] = ()
    query_fingerprint: str | None = None
    degraded: bool = False
    degradation_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "query_fingerprint": self.query_fingerprint,
            "degraded": self.degraded,
            "degradation_reason": self.degradation_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object] | None) -> MemoryContext:
        value = value or {}
        return cls(
            items=tuple(RetrievedMemory.from_dict(dict(item)) for item in value.get("items") or ()),
            query_fingerprint=(
                str(value["query_fingerprint"]) if value.get("query_fingerprint") else None
            ),
            degraded=bool(value.get("degraded")),
            degradation_reason=(
                str(value["degradation_reason"]) if value.get("degradation_reason") else None
            ),
        )


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    model: str
    version: str


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> EmbeddingResult: ...


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    kind: MemoryKind
    memory_type: str
    content: str
    source_type: MemorySource
    conflict_key: str | None = None
    correction: bool = False
    confirmed_by_user: bool = False
    confidence: float | None = None
    confidence_method: str | None = None
    retention_days: int | None = None


class MemoryCandidateExtractor(Protocol):
    def extract_candidates(
        self,
        *,
        user_text: str,
        assistant_text: str,
        outcome: str,
    ) -> tuple[MemoryCandidate, ...]: ...
