"""Authority-free contracts for semantic Goal and domain resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from property_agent.agent.react_contracts import contains_forbidden_authority_fields

GOAL_DOMAINS = frozenset({"repair", "billing", "announcement", "inspection"})
GOAL_CONSTRAINTS = frozenset({"lookup_existing_first"})


class GoalResolutionType(StrEnum):
    NEW = "NEW"
    CONTINUE = "CONTINUE"
    SWITCH = "SWITCH"
    CANCEL = "CANCEL"
    GENERAL_HELP = "GENERAL_HELP"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class GoalResolution:
    resolution: GoalResolutionType
    domain: str | None = None
    goal: str | None = None
    candidate_facts: dict[str, Any] = field(default_factory=dict)
    authorized_domains: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    question: str | None = None
    reason_code: str = ""

    def __post_init__(self) -> None:
        domains = set(self.authorized_domains)
        if domains - GOAL_DOMAINS:
            raise ValueError("Goal resolution contains an unknown authorized domain")
        if set(self.constraints) - GOAL_CONSTRAINTS:
            raise ValueError("Goal resolution contains an unknown constraint")
        if contains_forbidden_authority_fields(self.candidate_facts):
            raise ValueError("Goal facts contain server-owned authority fields")
        executable = {
            GoalResolutionType.NEW,
            GoalResolutionType.CONTINUE,
            GoalResolutionType.SWITCH,
        }
        if self.resolution in executable:
            if self.domain not in GOAL_DOMAINS or not str(self.goal or "").strip():
                raise ValueError("Executable Goal resolution requires domain and goal")
            if self.question:
                raise ValueError("Executable Goal resolution cannot ask a domain question")
        elif self.domain is not None or self.candidate_facts:
            raise ValueError("Non-executable Goal resolution cannot contain business facts")
        if self.resolution is GoalResolutionType.UNCERTAIN and not self.question:
            raise ValueError("UNCERTAIN Goal resolution requires a question")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GoalResolution:
        allowed = {
            "resolution",
            "domain",
            "goal",
            "candidate_facts",
            "authorized_domains",
            "constraints",
            "question",
            "reason_code",
        }
        if set(value) - allowed:
            raise ValueError("Goal resolution contains unknown fields")
        return cls(
            resolution=GoalResolutionType(value["resolution"]),
            domain=value.get("domain"),
            goal=value.get("goal"),
            candidate_facts=dict(value.get("candidate_facts") or {}),
            authorized_domains=tuple(value.get("authorized_domains") or ()),
            constraints=tuple(value.get("constraints") or ()),
            question=value.get("question"),
            reason_code=str(value.get("reason_code") or ""),
        )
