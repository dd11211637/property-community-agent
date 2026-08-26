"""Safe deterministic fault adapters for PR7-B chaos smoke coverage."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.application.langgraph_runtime import LangGraphEngine, build_saver_resource
from property_agent.agent.application.memory_writer import AcceptedEvidenceMemoryWriter
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.memory_contracts import MemoryCandidate, MemoryKind, MemorySource
from property_agent.platform.infrastructure.orm_models import Base
from tests.agent.test_pr4_langgraph_runtime import _supervisor, repair_state, runtime_fixture
from tests.agent.test_pr6_long_term_memory import (
    Context,
    _AcceptedHead,
    _Conversations,
    _Graph,
    _Recovery,
)


def test_c5_official_checkpoint_failure_never_advances_application_accepted_head(monkeypatch):
    resource = build_saver_resource(in_memory=True)
    runtime = runtime_fixture()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    accepted = SqlAlchemyCheckpointer(sessions)

    def fail_put(*_args, **_kwargs):
        raise RuntimeError("injected official saver failure")

    monkeypatch.setattr(resource.saver, "put", fail_put)
    graph = LangGraphEngine(
        resource.saver,
        _supervisor({"repair_list": lambda _request, _runtime: {"count": 0, "items": ()}}),
    )
    with pytest.raises(RuntimeError, match="saver failure"):
        graph.invoke(repair_state(runtime), thread_id=runtime.conversation_id, runtime=runtime)
    assert accepted.load_accepted(runtime.conversation_id) is None
    resource.close()
    engine.dispose()


def test_c10_memory_writer_persistence_failure_does_not_rollback_accepted_turn():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    class Extractor:
        def extract_candidates(self, **_kwargs):
            return (
                MemoryCandidate(
                    MemoryKind.SEMANTIC,
                    "PREFERENCE",
                    "使用简洁说明",
                    MemorySource.EXPLICIT_STATEMENT,
                ),
            )

    def failed_service(_session):
        raise RuntimeError("injected Memory persistence failure")

    writer = AcceptedEvidenceMemoryWriter(sessions, Extractor(), service_factory=failed_service)
    context = Context(uuid4(), uuid4(), frozenset())
    runner = AgentSessionRunner(
        graph=_Graph(),
        conversations=_Conversations(context),
        recovery=_Recovery(),
        checkpointer=_AcceptedHead(1),
        memory_writer=writer,
        enforce_concurrency=False,
    )
    turn = runner.start(conversation_id="pr7b-c10", context=context, user_text="记住偏好")
    assert turn.done is True
    assert turn.state.error is None
    engine.dispose()
