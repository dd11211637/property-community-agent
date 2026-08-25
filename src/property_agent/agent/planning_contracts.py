"""Bounded, authority-free semantic planning contracts for PR5."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PlanStepProposal:
    step_id: str
    goal: str
    domain: str
    specialist: str
    capability: str
    parameters: dict[str, Any] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    condition: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PlanStepProposal:
        required = {"step_id", "goal", "domain", "specialist", "capability"}
        allowed = required | {"parameters", "dependencies", "condition"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
            raise ValueError("planning step does not match the bounded schema")
        parameters = value.get("parameters") or {}
        dependencies = value.get("dependencies") or ()
        condition = value.get("condition")
        if not isinstance(parameters, dict) or not isinstance(dependencies, list | tuple):
            raise ValueError("planning step parameters or dependencies are invalid")
        if condition is not None and (
            not isinstance(condition, dict)
            or set(condition) != {"kind", "semantic_goal"}
            or not all(isinstance(item, str) for item in condition.values())
        ):
            raise ValueError("planning condition does not match the bounded schema")
        return cls(
            step_id=_required_text(value["step_id"], "step_id"),
            goal=_required_text(value["goal"], "goal"),
            domain=_required_text(value["domain"], "domain"),
            specialist=_required_text(value["specialist"], "specialist"),
            capability=_required_text(value["capability"], "capability"),
            parameters=dict(parameters),
            dependencies=tuple(_required_text(item, "dependency") for item in dependencies),
            condition=dict(condition) if condition is not None else None,
        )


@dataclass(frozen=True, slots=True)
class PlanProposal:
    objective_classification: str
    steps: tuple[PlanStepProposal, ...]
    provider: str = "unknown"

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, provider: str = "unknown") -> PlanProposal:
        if not isinstance(value, dict) or set(value) != {"objective_classification", "steps"}:
            raise ValueError("planning proposal does not match the bounded schema")
        if not isinstance(value["steps"], list):
            raise ValueError("planning proposal steps must be a list")
        return cls(
            objective_classification=_required_text(
                value["objective_classification"], "objective_classification"
            ),
            steps=tuple(PlanStepProposal.from_dict(item) for item in value["steps"]),
            provider=provider,
        )


class RelevanceDecision(StrEnum):
    MATCH = "match"
    NO_MATCH = "no-match"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class RelevanceJudgment:
    decision: RelevanceDecision
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RelevanceJudgment:
        if not isinstance(value, dict) or set(value) != {"decision", "evidence_refs"}:
            raise ValueError("relevance judgment does not match the bounded schema")
        refs = value["evidence_refs"]
        if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
            raise ValueError("relevance evidence references are invalid")
        return cls(RelevanceDecision(value["decision"]), tuple(refs))


@runtime_checkable
class PlanningGateway(Protocol):
    def propose_plan(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
        memory_context: dict[str, Any],
    ) -> PlanProposal: ...

    def judge_relevance(
        self,
        *,
        semantic_goal: str,
        evidence: dict[str, Any],
    ) -> RelevanceJudgment: ...


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"planning {field_name} must be non-empty text")
    return value.strip()
