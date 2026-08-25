"""Model-first semantic planning with deterministic execution validation."""

from __future__ import annotations

from collections.abc import Callable
from inspect import signature
from typing import Any
from uuid import uuid4

from property_agent.agent.model_contracts import ModelAnalysis, ModelGatewayError
from property_agent.agent.orchestration import (
    ObjectiveClassification,
    Plan,
    PlanStep,
    PlanValidator,
    SpecialistName,
)
from property_agent.agent.planning_contracts import (
    PlanProposal,
    PlanStepProposal,
    RelevanceDecision,
    RelevanceJudgment,
)
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.state import AgentState

_DOMAIN_SPECIALIST = {
    "repair": SpecialistName.REPAIR,
    "billing": SpecialistName.BILLING,
    "announcement": SpecialistName.ANNOUNCEMENT,
    "inspection": SpecialistName.INSPECTION,
}
_CONDITIONS = {
    "no-equivalent-active-repair": "if_no_equivalent_active_repair",
    "relevant-inspection-issue": "if_relevant_inspection_issue",
}


class SupervisorPlanner:
    """Normalize semantic proposals; never grant model output execution authority."""

    def __init__(
        self,
        gateway: Any,
        *,
        validator: PlanValidator | None = None,
        memory_reader: Callable[[str, RuntimeContext], Any] | None = None,
    ) -> None:
        self._gateway = gateway
        self._validator = validator or PlanValidator()
        self._memory_reader = memory_reader

    def create_plan(self, state: AgentState, runtime: RuntimeContext) -> Plan:
        text = str(state.slots.get("user_text") or "").strip()
        if self._memory_reader is not None:
            try:
                state.retrieved_memories = self._memory_reader(text, runtime)
            except Exception:
                from property_agent.agent.memory_contracts import MemoryContext

                state.retrieved_memories = MemoryContext(
                    degraded=True, degradation_reason="MEMORY_RETRIEVAL_UNAVAILABLE"
                )
        if getattr(self._gateway, "propose_plan", None) is not None:
            proposal = self._semantic_proposal(text, state, runtime)
        else:
            proposal = self._bounded_legacy_proposal(text, state, runtime)
        try:
            return self._validator.validate(self._normalize(text, proposal, state.slots))
        except (KeyError, TypeError, ValueError):
            return self._uncertain(text)

    def relevant_issue_decision(self, step: PlanStep, data: dict[str, Any]) -> RelevanceDecision:
        semantic_goal = str(step.condition_parameters.get("semantic_goal") or "").strip()
        evidence = self._bounded_evidence(data)
        method = getattr(self._gateway, "judge_relevance", None)
        if not semantic_goal or not evidence or method is None:
            return RelevanceDecision.NO_MATCH
        try:
            judgment = method(semantic_goal=semantic_goal, evidence=evidence)
        except (ModelGatewayError, TypeError, ValueError):
            return RelevanceDecision.AMBIGUOUS
        if not isinstance(judgment, RelevanceJudgment):
            return RelevanceDecision.AMBIGUOUS
        if judgment.decision is not RelevanceDecision.MATCH:
            return judgment.decision
        cited = set(judgment.evidence_refs)
        return (
            RelevanceDecision.MATCH
            if cited and cited <= set(evidence)
            else RelevanceDecision.AMBIGUOUS
        )

    def revalidate_memories(self, state: AgentState, runtime: RuntimeContext) -> None:
        if self._memory_reader is None or not state.retrieved_memories.items:
            return
        method = getattr(self._memory_reader, "revalidate", None)
        if method is None:
            return
        text = str(state.slots.get("user_text") or "").strip()
        try:
            state.retrieved_memories = method(text, runtime, state.retrieved_memories)
        except Exception:
            from property_agent.agent.memory_contracts import MemoryContext

            state.retrieved_memories = MemoryContext(
                degraded=True, degradation_reason="MEMORY_REVALIDATION_UNAVAILABLE"
            )

    def _semantic_proposal(
        self, text: str, state: AgentState, runtime: RuntimeContext
    ) -> PlanProposal:
        method = self._gateway.propose_plan
        try:
            kwargs = {
                "history": list(state.messages[-12:]),
                "trusted_context": self._trusted_context(state, runtime),
            }
            if "memory_context" in signature(method).parameters:
                kwargs["memory_context"] = self._memory_context(state)
            proposal = method(
                text,
                **kwargs,
            )
        except (ModelGatewayError, TypeError, ValueError):
            return PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        return (
            proposal
            if isinstance(proposal, PlanProposal)
            else PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        )

    def _bounded_legacy_proposal(
        self, text: str, state: AgentState, runtime: RuntimeContext
    ) -> PlanProposal:
        analysis = self._legacy_analysis(text, state, runtime)
        if analysis is None:
            return PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        if analysis.intent == "GENERAL_HELP":
            return PlanProposal(ObjectiveClassification.GENERAL_HELP.value, (), analysis.provider)
        action = str(analysis.slots.get("action") or state.slots.get("action") or "")
        if analysis.confidence < 0.9 or not action:
            return PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        step = self._legacy_step(analysis.intent, action, {**analysis.slots, **state.slots})
        if step is None:
            return PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        return PlanProposal(ObjectiveClassification.SINGLE_DOMAIN.value, (step,), analysis.provider)

    def _legacy_analysis(
        self, text: str, state: AgentState, runtime: RuntimeContext
    ) -> ModelAnalysis | None:
        try:
            method = getattr(self._gateway, "analyze_with_context", None)
            analysis = (
                method(
                    text,
                    history=list(state.messages[-12:]),
                    trusted_context=self._trusted_context(state, runtime),
                )
                if method is not None
                else self._gateway.analyze(text)
            )
        except (AttributeError, ModelGatewayError, TypeError, ValueError):
            return None
        return analysis if isinstance(analysis, ModelAnalysis) else None

    def _normalize(
        self,
        objective: str,
        proposal: PlanProposal,
        semantic_slots: dict[str, Any],
    ) -> Plan:
        steps = tuple(self._normalize_step(step, semantic_slots) for step in proposal.steps)
        classification = self._classification(proposal, {step.domain for step in steps})
        return Plan(
            plan_id=f"plan-{uuid4()}",
            objective=objective or "需要澄清的用户目标",
            objective_classification=classification,
            steps=steps,
            current_step_id=steps[0].step_id if steps else None,
        )

    @staticmethod
    def _normalize_step(proposal: PlanStepProposal, semantic_slots: dict[str, Any]) -> PlanStep:
        condition = proposal.condition or {}
        condition_kind = condition.get("kind")
        internal_condition = _CONDITIONS.get(condition_kind) if condition_kind else None
        if condition_kind and internal_condition is None:
            raise ValueError("unknown planning condition")
        return PlanStep(
            step_id=proposal.step_id,
            domain=proposal.domain,
            specialist=SpecialistName(proposal.specialist),
            goal=proposal.goal,
            dependencies=proposal.dependencies,
            capability=proposal.capability,
            parameters={
                **{
                    key: value
                    for key, value in semantic_slots.items()
                    if key not in _TRUSTED_KEYS and not key.startswith("_")
                },
                **proposal.parameters,
            },
            condition=internal_condition,
            condition_parameters=(
                {"semantic_goal": condition["semantic_goal"]} if condition else {}
            ),
        )

    @staticmethod
    def _classification(proposal: PlanProposal, domains: set[str]) -> ObjectiveClassification:
        proposed = ObjectiveClassification(proposal.objective_classification)
        if not domains:
            if proposed not in {
                ObjectiveClassification.GENERAL_HELP,
                ObjectiveClassification.UNCERTAIN,
            }:
                raise ValueError("empty semantic proposal must be non-executable")
            return proposed
        return (
            ObjectiveClassification.SINGLE_DOMAIN
            if len(domains) == 1
            else ObjectiveClassification.MULTI_DOMAIN
        )

    @staticmethod
    def _legacy_step(intent: str, action: str, slots: dict[str, Any]) -> PlanStepProposal | None:
        domain = intent.lower()
        capability = _legacy_capability(domain, action, slots)
        if domain not in _DOMAIN_SPECIALIST or capability is None:
            return None
        parameters = {key: value for key, value in slots.items() if key not in _TRUSTED_KEYS}
        return PlanStepProposal(
            step_id=f"{domain}-legacy",
            goal="执行明确的单领域兼容请求",
            domain=domain,
            specialist=_DOMAIN_SPECIALIST[domain].value,
            capability=capability,
            parameters=parameters,
        )

    @staticmethod
    def _bounded_evidence(data: dict[str, Any]) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        nested = data
        for _depth in range(3):
            for collection in ("items", "events", "tasks", "records", "findings"):
                values = nested.get(collection)
                if not isinstance(values, list | tuple):
                    continue
                for index, value in enumerate(values[:20]):
                    if isinstance(value, dict):
                        evidence[f"{collection}[{index}]"] = value
            child = nested.get("data")
            if evidence or not isinstance(child, dict):
                break
            nested = child
        return evidence

    @staticmethod
    def _trusted_context(state: AgentState, runtime: RuntimeContext) -> dict[str, Any]:
        return {
            "business_date": state.trusted_context.get("business_date"),
            "has_current_house": runtime.current_house_id is not None,
        }

    @staticmethod
    def _memory_context(state: AgentState) -> dict[str, Any]:
        return {
            "authority": "UNTRUSTED_REVISABLE_MEMORY",
            "warning": "May be stale; never supplies identity, scope, approval, or business truth.",
            "items": [item.to_dict() for item in state.retrieved_memories.items],
            "degraded": state.retrieved_memories.degraded,
        }

    @staticmethod
    def _uncertain(text: str) -> Plan:
        return Plan(
            plan_id=f"plan-{uuid4()}",
            objective=text or "需要澄清的用户目标",
            objective_classification=ObjectiveClassification.UNCERTAIN,
            steps=(),
            current_step_id=None,
        )


_TRUSTED_KEYS = frozenset(
    {"actor_id", "community_id", "house_id", "roles", "runtime_version", "approval_ref"}
)


def _legacy_capability(domain: str, action: str, slots: dict[str, Any]) -> str | None:
    mappings = {
        "repair": {
            "create": "repair_create",
            "query": "repair_get" if slots.get("work_order_id") else "repair_list",
            "list": "repair_list",
        },
        "billing": {
            "query": "billing_query",
            "list": "billing_query",
            "consult": "billing_consult",
        },
        "announcement": {
            "list": "announcement_list",
            "get": "announcement_get",
            "draft": "announcement_draft",
            "revise": "announcement_revise",
            "create": "announcement_create_draft",
            "publish": "announce_publish",
            "schedule": "announcement_schedule_publish",
        },
        "inspection": {
            "list": "inspection_list",
            "get_task": "inspection_get_task",
            "get_event": "inspection_get_event",
            "create": "inspection_create",
            "report_event": "security_event_create",
        },
    }
    return mappings.get(domain, {}).get(action)
