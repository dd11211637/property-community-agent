"""Deterministic server-owned retention policy for automatic Memory candidates."""

from dataclasses import dataclass

from property_agent.agent.memory_contracts import MemoryCandidate, MemoryKind


@dataclass(frozen=True, slots=True)
class RetentionRule:
    default_days: int
    maximum_days: int


_RULES = {
    MemoryKind.EPISODIC: RetentionRule(default_days=90, maximum_days=180),
    MemoryKind.PROCEDURAL_CANDIDATE: RetentionRule(default_days=30, maximum_days=60),
    MemoryKind.SEMANTIC: RetentionRule(default_days=365, maximum_days=730),
}


def governed_retention_days(candidate: MemoryCandidate) -> int | None:
    """Return server-governed retention; provider values may only shorten it."""
    if candidate.kind is MemoryKind.SEMANTIC and candidate.confirmed_by_user:
        proposed = candidate.retention_days
        if proposed is None:
            return None
        return proposed if 0 < proposed <= _RULES[MemoryKind.SEMANTIC].maximum_days else None
    rule = _RULES[candidate.kind]
    proposed = candidate.retention_days
    if proposed is None or proposed <= 0:
        return rule.default_days
    return min(proposed, rule.maximum_days)
