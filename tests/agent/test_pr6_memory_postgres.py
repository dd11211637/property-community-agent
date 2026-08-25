"""Real PostgreSQL + pgvector acceptance for PR6 Memory."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.infrastructure.models import AgentMemoryModel
from property_agent.agent.memory_contracts import (
    EmbeddingResult,
    MemoryCandidate,
    MemoryKind,
    MemoryQuery,
    MemorySource,
)
from property_agent.platform.infrastructure.orm_models import Base

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="requires TEST_POSTGRES_URL with pgvector"),
]


@dataclass(frozen=True)
class Context:
    actor_id: UUID
    community_id: UUID
    house_ids: frozenset[UUID]


class SemanticEmbedding:
    def __init__(self, *, model: str = "test-semantic", version: str = "1") -> None:
        self._model = model
        self._version = version

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    def embed(self, content: str) -> EmbeddingResult:
        vector = [0.0] * 1536
        vector[0 if "联系" in content or "站内" in content else 1] = 1.0
        return EmbeddingResult(tuple(vector), self.model, self.version)


@pytest.fixture
def sessions():
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _query(context: Context, house: UUID, text_value: str) -> MemoryQuery:
    return MemoryQuery(
        text=text_value,
        actor_id=context.actor_id,
        community_id=context.community_id,
        current_house_id=house,
        bound_house_ids=context.house_ids,
    )


def test_pgvector_semantic_ranking_runs_after_scope_filter(sessions):
    house = uuid4()
    context = Context(uuid4(), uuid4(), frozenset({house}))
    other = Context(uuid4(), context.community_id, frozenset({house}))
    with sessions() as session:
        service = AgentMemoryService(session, embedding_provider=SemanticEmbedding())
        notice = service.create_memory(
            context, memory_type="PREFERENCE", content="社区通知尽量简洁", house_id=house
        )
        service.create_memory(
            context, memory_type="COMMUNICATION", content="上门前用站内消息联系", house_id=house
        )
        ranked = service.retrieve(_query(context, house, "维修人员如何提前联系"))
        leaked = service.retrieve(_query(other, house, "维修人员如何提前联系"))
        extension = session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        ).scalar_one()
        service.delete_memory(UUID(notice["id"]), context, expected_version=notice["version"])
        after_delete = service.retrieve(_query(context, house, "社区通知风格"))
        deleted_row = session.get(AgentMemoryModel, UUID(notice["id"]))
    assert ranked.items[0].content == "上门前用站内消息联系"
    assert ranked.items[0].semantic_score is not None
    assert leaked.items == ()
    assert extension
    assert all(item.memory_id != UUID(notice["id"]) for item in after_delete.items)
    assert deleted_row.embedding is None
    assert deleted_row.embedding_status == "DELETED"


def test_concurrent_corrections_leave_exactly_one_effective_record(sessions):
    context = Context(uuid4(), uuid4(), frozenset())
    original = MemoryCandidate(
        MemoryKind.SEMANTIC,
        "COMMUNICATION",
        "上门前打电话",
        MemorySource.EXPLICIT_STATEMENT,
        conflict_key="contact-channel",
    )
    with sessions() as session:
        AgentMemoryService(session).persist_candidate(
            context,
            candidate=original,
            source_evidence_id="accepted:1",
            provenance={"conversation_id": "conv-pg", "accepted_head_version": 1},
            house_id=None,
        )
    barrier = threading.Barrier(2)
    failures: list[Exception] = []

    def correct(index: int) -> None:
        candidate = MemoryCandidate(
            MemoryKind.SEMANTIC,
            "COMMUNICATION",
            f"只发站内消息（修正{index}）",
            MemorySource.USER_CORRECTION,
            conflict_key="contact-channel",
            correction=True,
        )
        try:
            barrier.wait(timeout=5)
            with sessions() as session:
                AgentMemoryService(session).persist_candidate(
                    context,
                    candidate=candidate,
                    source_evidence_id=f"accepted:{index + 2}",
                    provenance={"conversation_id": "conv-pg", "accepted_head_version": index + 2},
                    house_id=None,
                )
        except Exception as exc:  # surfaced below with the concrete failure
            failures.append(exc)

    threads = [threading.Thread(target=correct, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    with sessions() as session:
        rows = list(
            session.scalars(
                select(AgentMemoryModel).where(
                    AgentMemoryModel.actor_id == context.actor_id,
                    AgentMemoryModel.community_id == context.community_id,
                )
            )
        )
    assert failures == []
    assert len([row for row in rows if row.lifecycle_status == "ACTIVE"]) == 1
    assert len([row for row in rows if row.lifecycle_status == "SUPERSEDED"]) == 2


def test_bounded_reindex_promotes_pending_and_refreshes_old_model_version(sessions):
    house = uuid4()
    context = Context(uuid4(), uuid4(), frozenset({house}))
    with sessions() as session:
        pending_service = AgentMemoryService(session)
        created = pending_service.create_memory(
            context,
            memory_type="COMMUNICATION",
            content="上门前用站内消息联系",
            house_id=house,
        )
        row = session.get(AgentMemoryModel, UUID(created["id"]))
        assert row.embedding_status == "PENDING"

        v1 = AgentMemoryService(session, embedding_provider=SemanticEmbedding(version="1"))
        first = v1.reindex_memories(limit=1)
        session.refresh(row)
        assert (first.selected, first.ready, first.failed, first.remaining) == (1, 1, 0, 0)
        assert row.embedding_status == "READY"
        assert v1.retrieve(_query(context, house, "维修人员如何联系")).items[0].memory_id == row.id

        canonical_before = (
            row.version,
            row.content,
            dict(row.provenance),
            row.lifecycle_status,
            row.source_type,
            row.source_evidence_id,
        )
        v2 = AgentMemoryService(session, embedding_provider=SemanticEmbedding(version="2"))
        refreshed = v2.reindex_memories(limit=10)
        session.refresh(row)
        canonical_after = (
            row.version,
            row.content,
            dict(row.provenance),
            row.lifecycle_status,
            row.source_type,
            row.source_evidence_id,
        )
    assert refreshed.selected == 1
    assert refreshed.ready == 1
    assert refreshed.remaining == 0
    assert row.embedding_version == "2"
    assert canonical_after == canonical_before
