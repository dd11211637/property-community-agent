from types import SimpleNamespace
from uuid import uuid4

from property_agent.agent.application.embedding import EmbeddingUnavailable
from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.observability import AgentObservability
from property_agent.agent.observed_boundaries import ObservedMemoryReader
from tests.agent.test_pr6_long_term_memory import Context, _factory, _query


class FailingEmbedding:
    model = "pr7b-test-embedding"
    version = "1"

    def embed(self, _text):
        raise EmbeddingUnavailable("injected provider outage")


def test_embedding_vector_outage_uses_only_structured_scope_safe_results():
    engine, factory = _factory()
    house = uuid4()
    owner = Context(uuid4(), uuid4(), frozenset({house}))
    outsider = Context(uuid4(), owner.community_id, frozenset({house}))
    with factory() as session:
        AgentMemoryService(session).create_memory(
            owner,
            memory_type="COMMUNICATION",
            content="上门前发送站内通知",
            house_id=house,
        )
    with factory() as session:
        service = AgentMemoryService(session, embedding_provider=FailingEmbedding())
        observation = AgentObservability.in_memory()
        reader = ObservedMemoryReader(
            lambda _text, _runtime: service.retrieve(_query(owner, house)), observation
        )
        runtime = SimpleNamespace(observation=SimpleNamespace(runtime_version="v1"))
        result = reader("联系偏好", runtime)
        assert [item.content for item in result.items] == ["上门前发送站内通知"]
        assert result.degraded is True
        assert result.degradation_reason == "EMBEDDING_OR_VECTOR_UNAVAILABLE"
        assert service.retrieve(_query(outsider, house)).items == ()
    engine.dispose()


def test_reindex_failure_keeps_canonical_memory_and_exposes_failed_backlog():
    engine, factory = _factory()
    house = uuid4()
    owner = Context(uuid4(), uuid4(), frozenset({house}))
    with factory() as session:
        created = AgentMemoryService(session).create_memory(
            owner,
            memory_type="PREFERENCE",
            content="使用简洁回复",
            house_id=house,
        )
    with factory() as session:
        service = AgentMemoryService(session, embedding_provider=FailingEmbedding())
        result = service.reindex_memories(limit=10)
        listed = service.list_memories(owner)
    assert result.selected == 1
    assert result.ready == 0
    assert result.failed == 1
    assert result.remaining == 1
    assert listed[0]["id"] == created["id"]
    assert listed[0]["lifecycle_status"] == "ACTIVE"
    assert listed[0]["embedding_status"] == "FAILED"
    engine.dispose()
