"""Safe deterministic fault adapters for PR7-B chaos smoke coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.application.langgraph_runtime import LangGraphEngine, build_saver_resource
from property_agent.agent.application.memory_writer import AcceptedEvidenceMemoryWriter
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.run_lease import Lease, StaleAgentRunError
from property_agent.agent.memory_contracts import MemoryCandidate, MemoryKind, MemorySource
from property_agent.platform.infrastructure.orm_models import Base
from tests.agent.test_pr4_langgraph_runtime import _supervisor, repair_state, runtime_fixture
from tests.agent.test_pr6_long_term_memory import (
    Context,
    _AcceptedHead,
    _Conversations,
    _Graph,
    _Recovery,
    _WriterSpy,
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


def test_c12_runner_rejects_stale_candidate_before_canonical_publication(monkeypatch):
    context = Context(uuid4(), uuid4(), frozenset())
    conversation_id = "pr7b-c12-runner"

    class CandidateGraph(_Graph):
        internal_candidate = None

        def invoke(self, state, **kwargs):
            result = super().invoke(state, **kwargs)
            self.internal_candidate = result["state"]
            return result

    class AcceptedHead:
        version = 7
        published = 0

        def version_of(self, _thread_id):
            return self.version

        def publish_accepted(self, *_args, **_kwargs):
            self.published += 1
            return self.version + 1

    class LeaseService:
        def __init__(self):
            self.current = self._lease(1)

        def _lease(self, fence):
            return Lease(
                conversation_id,
                uuid4(),
                fence,
                datetime.now(timezone.utc) + timedelta(seconds=30),
            )

        def acquire(self, _thread_id, *, run_id):
            self.current = Lease(
                conversation_id, run_id, 1, datetime.now(timezone.utc) + timedelta(seconds=30)
            )
            return self.current

        def renew(self, *_args, **_kwargs):
            return self.current

        def release(self, *_args, **_kwargs):
            return None

        def replace(self):
            self.current = self._lease(2)
            return self.current

    graph = CandidateGraph()
    accepted = AcceptedHead()
    leases = LeaseService()
    writer = _WriterSpy()
    business_mutations = []
    runner = AgentSessionRunner(
        graph=graph,
        conversations=_Conversations(context),
        recovery=_Recovery(),
        checkpointer=accepted,
        run_lease=leases,
        memory_writer=writer,
        turn_recorder=lambda *_args: business_mutations.append("mutation"),
        enforce_concurrency=True,
    )
    monkeypatch.setattr(runner._turn_guard, "start_heartbeat", lambda _lease: None)

    def reject_after_engine(_lease, _heartbeat):
        replacement = leases.replace()
        assert replacement.fence == 2
        raise StaleAgentRunError(conversation_id)

    monkeypatch.setattr(runner._turn_guard, "assert_alive", reject_after_engine)
    with pytest.raises(StaleAgentRunError):
        runner.start(conversation_id=conversation_id, context=context, user_text="候选结果")
    assert graph.internal_candidate is not None
    assert accepted.version == 7
    assert accepted.published == 0
    assert writer.calls == 0
    assert business_mutations == []
