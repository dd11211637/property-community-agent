"""Provider-contract fakes for deterministic PR5 semantic-planning tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from property_agent.agent.planning_contracts import (
    PlanProposal,
    PlanStepProposal,
    RelevanceDecision,
    RelevanceJudgment,
)


@dataclass
class StaticPlanningGateway:
    proposal: PlanProposal
    relevance: RelevanceJudgment = field(
        default_factory=lambda: RelevanceJudgment(RelevanceDecision.NO_MATCH)
    )
    requests: list[dict[str, Any]] = field(default_factory=list)

    def propose_plan(self, text, *, history, trusted_context):
        self.requests.append(
            {"text": text, "history": list(history), "trusted_context": dict(trusted_context)}
        )
        return self.proposal

    def judge_relevance(self, *, semantic_goal, evidence):
        self.requests.append({"semantic_goal": semantic_goal, "evidence": dict(evidence)})
        return self.relevance


def proposal(*steps: PlanStepProposal, classification: str | None = None) -> PlanProposal:
    domains = {step.domain for step in steps}
    inferred = "single-domain" if len(domains) == 1 else "multi-domain"
    if not steps:
        inferred = "uncertain"
    return PlanProposal(classification or inferred, tuple(steps), "provider-contract-fake")


def step(
    step_id: str,
    domain: str,
    capability: str,
    goal: str,
    *,
    parameters: dict[str, Any] | None = None,
    dependencies: tuple[str, ...] = (),
    condition: dict[str, str] | None = None,
) -> PlanStepProposal:
    specialists = {
        "repair": "RepairSpecialist",
        "billing": "BillingSpecialist",
        "announcement": "AnnouncementSpecialist",
        "inspection": "InspectionSpecialist",
    }
    return PlanStepProposal(
        step_id,
        goal,
        domain,
        specialists[domain],
        capability,
        parameters or {},
        dependencies,
        condition,
    )
