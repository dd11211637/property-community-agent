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
from property_agent.agent.infrastructure.run_lease import RunLeaseService, StaleAgentRunError
from property_agent.agent.memory_contracts import MemoryCandidate, MemoryKind, MemorySource
from property_agent.agent.observability import AgentObservability
from property_agent.agent.observed_boundaries import ObservedMemoryWriter
from property_agent.agent.state import AgentState
from property_agent.agent.stream_events import StreamEventKind
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
from tests.support_graph_engine import TestGraphEngine


def test_c5_official_checkpoint_failure_never_advances_application_accepted_head(monkeypatch):
    observation = AgentObservability.in_memory()
    resource = build_saver_resource(in_memory=True, observability=observation)
    runtime = runtime_fixture()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    accepted = SqlAlchemyCheckpointer(sessions)

    def fail_put(*_args, **_kwargs):
        raise RuntimeError("injected official saver failure")

    monkeypatch.setattr(resource.saver._delegate, "put", fail_put)
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

    observation = AgentObservability.in_memory()
    writer = ObservedMemoryWriter(
        AcceptedEvidenceMemoryWriter(sessions, Extractor(), service_factory=failed_service),
        observation,
    )
    context = Context(uuid4(), uuid4(), frozenset())
    runner = AgentSessionRunner(
        engine=TestGraphEngine(_Graph()),
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


@pytest.mark.parametrize("operation", ["start", "stream_start", "resume", "stream_resume"])
def test_c12_runner_rejects_stale_candidate_before_canonical_publication(operation):
    context = Context(uuid4(), uuid4(), frozenset())
    conversation_id = f"pr7b-c12-{operation}"

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    class RecordingRunLeaseService(RunLeaseService):
        acquired = []

        def acquire(self, *args, **kwargs):
            lease = super().acquire(*args, **kwargs)
            self.acquired.append(lease)
            return lease

    leases = RecordingRunLeaseService(sessions, lease_seconds=30)

    class CandidateEngine:
        internal_candidate = None
        replacement = None

        def _complete_after_replacement(self, state):
            state.add_message("assistant", "候选结果")
            self.internal_candidate = state
            original = leases.acquired[0]
            leases.release(conversation_id, original.run_id)
            self.replacement = leases.acquire(conversation_id, run_id=uuid4())
            assert self.replacement.fence == original.fence + 1
            return {"state": state, "interrupt": None, "done": True}

        def invoke(self, state, **_kwargs):
            return self._complete_after_replacement(state)

        def invoke_stream(self, state, **_kwargs):
            yield "__final__", self._complete_after_replacement(state)

        def resume(self, _thread_id, _resume_value, *, state, **_kwargs):
            return self._complete_after_replacement(state)

        def resume_stream(self, _thread_id, _resume_value, *, state, **_kwargs):
            yield "__final__", self._complete_after_replacement(state)

    class AcceptedHead:
        version = 7
        published = 0

        def version_of(self, _thread_id):
            return self.version

        def publish_accepted(self, *_args, **_kwargs):
            self.published += 1
            return self.version + 1

        def load_accepted(self, _conversation_id):
            return None

    class Conversations(_Conversations):
        sync_calls = 0

        def sync_from_state(self, state, *, waiting_confirm):
            self.sync_calls += 1
            return super().sync_from_state(state, waiting_confirm=waiting_confirm)

    candidate_engine = CandidateEngine()
    accepted = AcceptedHead()
    writer = _WriterSpy()
    business_mutations = []
    conversations = Conversations(context)
    restored = AgentState(conversation_id=conversation_id)
    runner = AgentSessionRunner(
        engine=TestGraphEngine(candidate_engine),
        conversations=conversations,
        recovery=_Recovery(restored),
        checkpointer=accepted,
        run_lease=leases,
        memory_writer=writer,
        turn_recorder=lambda *_args: business_mutations.append("mutation"),
        enforce_concurrency=True,
        heartbeat_interval_seconds=3600,
    )
    delivered = []

    with pytest.raises(StaleAgentRunError):
        if operation == "start":
            runner.start(
                conversation_id=conversation_id,
                context=context,
                user_text="候选结果",
            )
        elif operation == "stream_start":
            delivered.extend(
                runner.stream_start(
                    conversation_id=conversation_id,
                    context=context,
                    user_text="候选结果",
                )
            )
        elif operation == "resume":
            runner.resume(
                conversation_id=conversation_id,
                context=context,
                confirmed=False,
            )
        else:
            delivered.extend(
                runner.stream_resume(
                    conversation_id=conversation_id,
                    context=context,
                    confirmed=False,
                )
            )
    assert candidate_engine.internal_candidate is not None
    assert candidate_engine.replacement is not None
    assert accepted.version == 7
    assert accepted.published == 0
    assert conversations.sync_calls == 0
    assert writer.calls == 0
    assert business_mutations == []
    assert all(event.kind is not StreamEventKind.FINAL for event in delivered)
    assert any(
        point.name == "agent_lease_operation_total"
        and point.attributes == {"operation": "renew", "outcome": "lost"}
        for point in runner._observability.points
    )
    engine.dispose()
