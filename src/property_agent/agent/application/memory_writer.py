"""Accepted-evidence Memory Writer orchestration."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from property_agent.agent.application.memory_service import AgentMemoryService, MemoryContext
from property_agent.agent.memory_contracts import MemoryCandidate, MemoryCandidateExtractor
from property_agent.agent.state import AgentState

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]

_SENSITIVE = re.compile(
    r"(?i)(password|passwd|secret|api[_ -]?key|confirmation[_ -]?token|"
    r"idempotency[_ -]?key|bearer\s+[a-z0-9._-]+|密码|口令|令牌)"
)
_AUTHORITY_CLAIM = re.compile(
    r"(?i)(\badmin(?:istrator)?\b|\bapproved\b|\bauthori[sz]ed\b|"
    r"管理员|已批准|有权审批|无需确认)"
)


@dataclass(frozen=True, slots=True)
class WriterResult:
    source_evidence_id: str
    proposed: int
    stored: int
    rejected: int
    degraded: bool = False


class AcceptedEvidenceMemoryWriter:
    """Extract and persist bounded candidates only after accepted-head publication."""

    def __init__(
        self,
        session_factory: SessionFactory,
        extractor: MemoryCandidateExtractor,
        *,
        service_factory: Callable[[Session], AgentMemoryService] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._extractor = extractor
        self._service_factory = service_factory or AgentMemoryService

    def write_accepted_turn(
        self,
        *,
        context: MemoryContext,
        state: AgentState,
        user_text: str,
        assistant_text: str,
        accepted_version: int,
    ) -> WriterResult:
        source_id = f"accepted-head:{state.conversation_id}:{accepted_version}"
        outcome = self._outcome(state)
        try:
            candidates = self._extractor.extract_candidates(
                user_text=user_text[:2000],
                assistant_text=assistant_text[:2000],
                outcome=outcome,
            )
        except Exception:
            logger.exception("memory_writer_extraction_failed", extra={"source_id": source_id})
            return WriterResult(source_id, 0, 0, 0, degraded=True)
        eligible = tuple(
            candidate for candidate in candidates[:8] if self._eligible(candidate, outcome)
        )
        stored = 0
        try:
            with self._sessions() as session:
                service = self._service_factory(session)
                for candidate in eligible:
                    service.persist_candidate(
                        context,
                        candidate=candidate,
                        source_evidence_id=source_id,
                        provenance={
                            "accepted_head_version": accepted_version,
                            "conversation_id": state.conversation_id,
                            "outcome": outcome,
                        },
                        house_id=state.current_house_id,
                    )
                    stored += 1
        except Exception:
            logger.exception("memory_writer_persistence_failed", extra={"source_id": source_id})
            return WriterResult(source_id, len(candidates), stored, len(candidates) - stored, True)
        return WriterResult(
            source_id,
            proposed=len(candidates),
            stored=stored,
            rejected=len(candidates) - len(eligible),
        )

    @staticmethod
    def _eligible(candidate: MemoryCandidate, outcome: str) -> bool:
        content = candidate.content.strip()
        if (
            not content
            or len(content) > 500
            or _SENSITIVE.search(content)
            or _AUTHORITY_CLAIM.search(content)
        ):
            return False
        if candidate.kind.value == "EPISODIC" and (
            candidate.source_type.value != "COMPLETED_PLAN" or outcome != "completed"
        ):
            return False
        if candidate.kind.value == "PROCEDURAL_CANDIDATE" and candidate.confirmed_by_user:
            return False
        if candidate.kind.value == "SEMANTIC" and candidate.memory_type == "SERVICE_NOTE":
            return False
        return candidate.memory_type in {
            "PREFERENCE",
            "COMMUNICATION",
            "ACCESSIBILITY",
            "SERVICE_NOTE",
        }

    @staticmethod
    def _outcome(state: AgentState) -> str:
        if state.plan is None:
            return "completed" if not state.error else "failed"
        value = state.plan.status.value.lower()
        return {
            "waiting-confirmation": "pending",
            "needs-clarification": "partial",
            "handover": "partial",
        }.get(value, value)


class NullMemoryCandidateExtractor:
    """Configured no-write fallback; it never invents candidates."""

    def extract_candidates(self, **_kwargs: Any) -> tuple[MemoryCandidate, ...]:
        return ()
