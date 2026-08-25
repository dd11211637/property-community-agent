"""Bounded embedding maintenance for canonical Memory records."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from property_agent.agent.infrastructure.models import AgentMemoryModel
from property_agent.agent.memory_contracts import (
    EmbeddingProvider,
    MemoryLifecycle,
    ReindexResult,
)


class MemoryReindexer:
    """Refresh embedding projections without changing canonical Memory semantics."""

    def __init__(
        self,
        session: Session,
        provider: EmbeddingProvider | None,
        index_record: Callable[[AgentMemoryModel], None],
        *,
        clock: Callable[[], object],
    ) -> None:
        self._session = session
        self._provider = provider
        self._index_record = index_record
        self._clock = clock

    def run(self, *, limit: int) -> ReindexResult:
        bounded = max(1, min(limit, 100))
        model = getattr(self._provider, "model", None)
        version = getattr(self._provider, "version", None)
        if not model or not version:
            return ReindexResult(0, 0, 0, self._remaining(None, None), degraded=True)
        rows = list(
            self._session.execute(
                select(AgentMemoryModel)
                .where(*self._filters(model, version))
                .order_by(AgentMemoryModel.updated_at, AgentMemoryModel.id)
                .limit(bounded)
                .with_for_update(skip_locked=True)
            ).scalars()
        )
        ready = 0
        for row in rows:
            self._index_record(row)
            ready += int(row.embedding_status == "READY")
        self._session.commit()
        return ReindexResult(
            selected=len(rows),
            ready=ready,
            failed=len(rows) - ready,
            remaining=self._remaining(model, version),
        )

    def _filters(self, model: str, version: str) -> tuple[object, ...]:
        return (
            AgentMemoryModel.lifecycle_status == MemoryLifecycle.ACTIVE.value,
            AgentMemoryModel.deleted_at.is_(None),
            or_(AgentMemoryModel.expires_at.is_(None), AgentMemoryModel.expires_at > self._clock()),
            or_(
                AgentMemoryModel.embedding_status.in_(("PENDING", "FAILED")),
                AgentMemoryModel.embedding_model.is_(None),
                AgentMemoryModel.embedding_model != model,
                AgentMemoryModel.embedding_version.is_(None),
                AgentMemoryModel.embedding_version != version,
            ),
        )

    def _remaining(self, model: str | None, version: str | None) -> int:
        filters = (
            self._filters(model, version)
            if model and version
            else (
                AgentMemoryModel.lifecycle_status == MemoryLifecycle.ACTIVE.value,
                AgentMemoryModel.deleted_at.is_(None),
                AgentMemoryModel.embedding_status.in_(("PENDING", "FAILED")),
            )
        )
        return int(
            self._session.execute(
                select(func.count()).select_from(AgentMemoryModel).where(*filters)
            ).scalar_one()
        )
