"""Bounded provider output parsing for accepted-evidence Memory candidates."""

from typing import Any

from property_agent.agent.memory_contracts import MemoryCandidate, MemoryKind, MemorySource
from property_agent.agent.model_contracts import ModelGatewayError

MEMORY_WRITER_PROMPT = """Extract only durable, future-useful long-term memory candidates
from one accepted property-assistant turn. Return JSON only: {"candidates":[]} where each
candidate has kind (SEMANTIC|EPISODIC|PROCEDURAL_CANDIDATE), memory_type
(PREFERENCE|COMMUNICATION|ACCESSIBILITY|SERVICE_NOTE), content, source_type
(EXPLICIT_STATEMENT|USER_CORRECTION|COMPLETED_PLAN), conflict_key or null, correction,
confidence, confidence_method, and retention_days or null. Never copy full transcripts.
Never store credentials, tokens, identity/role claims, approval, leases/fences, hidden
reasoning, or unsupported model guesses. Failed/cancelled/pending actions are not successful
episodes. Transient one-off requests are normally ineligible. Use at most 4 candidates.
"""


def parse_memory_candidates(value: Any, user_text: str) -> tuple[MemoryCandidate, ...]:
    rows = value.get("candidates") if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) > 4:
        raise ModelGatewayError("memory candidates do not match the bounded schema")
    return tuple(_candidate(row, user_text) for row in rows if isinstance(row, dict))


def _candidate(row: dict[str, Any], user_text: str) -> MemoryCandidate:
    content = str(row["content"]).strip()
    source_type = str(row["source_type"])
    return MemoryCandidate(
        kind=MemoryKind(row["kind"]),
        memory_type=str(row["memory_type"]),
        content=content,
        source_type=MemorySource(source_type),
        conflict_key=str(row["conflict_key"]) if row.get("conflict_key") else None,
        correction=bool(row.get("correction")),
        confirmed_by_user=_directly_entailed(content, user_text, source_type),
        confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
        confidence_method=(str(row["confidence_method"]) if row.get("confidence_method") else None),
        retention_days=(
            int(row["retention_days"]) if row.get("retention_days") is not None else None
        ),
    )


def _directly_entailed(content: str, user_text: str, source_type: str) -> bool:
    if source_type not in {"EXPLICIT_STATEMENT", "USER_CORRECTION"}:
        return False
    normalized_content = "".join(content.lower().split())
    normalized_user = "".join(user_text.lower().split())
    return bool(normalized_content and normalized_content in normalized_user)
