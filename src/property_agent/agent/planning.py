"""Model-first semantic planning with deterministic execution validation."""

from __future__ import annotations

from collections.abc import Callable
from inspect import signature
from typing import Any
from uuid import uuid4

from property_agent.agent.deterministic_gateway import (
    deterministic_inspection_slots,
    deterministic_repair_slots,
)
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
        guided = self._guided_announcement_proposal(text, state)
        if guided is None:
            guided = self._deterministic_inspection_proposal(text, state)
        if guided is None:
            guided = self._deterministic_repair_continuation(state)
        if guided is not None:
            proposal = guided
        elif getattr(self._gateway, "propose_plan", None) is not None:
            proposal = self._semantic_proposal(text, state, runtime)
        else:
            proposal = self._bounded_analysis_proposal(text, state, runtime)
        try:
            return self._validator.validate(self._normalize(text, proposal, state.slots))
        except (KeyError, TypeError, ValueError):
            return self._uncertain(text)

    @staticmethod
    def _guided_announcement_proposal(text: str, state: AgentState) -> PlanProposal | None:
        """Start or continue a new announcement without asking for internal IDs."""

        if state.slots.get("announcement_id"):
            return None
        compact_text = "".join(text.split())
        start_markers = (
            "我要发布公告",
            "发布公告",
            "发公告",
            "新建公告",
            "创建公告",
            "我要发通知",
            "发布通知",
        )
        continuing = state.intent == "ANNOUNCEMENT" and any(
            key in state.slots and state.slots[key] is not None
            for key in ("title", "body", "audience")
        )
        if not continuing and not any(marker in compact_text for marker in start_markers):
            return None
        parameters = {"action": "create"}
        for key in ("title", "body", "audience"):
            if key in state.slots and state.slots[key] is not None:
                parameters[key] = state.slots[key]
        step = PlanStepProposal(
            step_id="announcement-guided-create-v2",
            goal="分步收集公告内容并保存为待审核草稿",
            domain="announcement",
            specialist=SpecialistName.ANNOUNCEMENT.value,
            capability="announcement_create_draft",
            parameters=parameters,
        )
        return PlanProposal(
            ObjectiveClassification.SINGLE_DOMAIN.value,
            (step,),
            "deterministic-guided-announcement",
        )

    @staticmethod
    def _deterministic_inspection_proposal(text: str, state: AgentState) -> PlanProposal | None:
        """Stabilize the public task-list and guided task-creation entry points."""

        if any(marker in text for marker in ("公告", "通知")):
            return None
        existing_action = state.slots.get("action")
        if existing_action not in {None, "create", "query"}:
            return None
        detected = deterministic_inspection_slots(text)
        continuing_create = (
            state.intent == "INSPECTION"
            and state.slots.get("action") == "create"
            and not state.slots.get("task_id")
        )
        action = "create" if continuing_create else detected.get("action")
        if action not in {"create", "query"}:
            return None
        capability = "inspection_create" if action == "create" else "inspection_list"
        parameters: dict[str, Any] = {
            "action": action,
            "target": "task",
        }
        if action == "query":
            parameters.update(
                statuses=tuple(state.slots.get("statuses") or ()),
                assigned_to_me=bool(state.slots.get("assigned_to_me", False)),
                limit=int(state.slots.get("limit") or 20),
            )
        else:
            for key in ("title", "description", "point", "route_points"):
                if key in state.slots and state.slots[key] is not None:
                    parameters[key] = state.slots[key]
        step = PlanStepProposal(
            step_id=f"inspection-{action}-v2",
            goal="查询巡检任务状态" if action == "query" else "分步填写并创建巡检任务",
            domain="inspection",
            specialist=SpecialistName.INSPECTION.value,
            capability=capability,
            parameters=parameters,
        )
        return PlanProposal(
            ObjectiveClassification.SINGLE_DOMAIN.value,
            (step,),
            "deterministic-inspection-entry",
        )

    @staticmethod
    def _deterministic_repair_continuation(state: AgentState) -> PlanProposal | None:
        """Continue an in-progress repair deterministically.

        ``prepare_start_state`` (``domain_continuation``) sets
        ``state.intent = "REPAIR"`` and copies the awaited-slot reply plus the
        carried domain slots into ``state.slots`` whenever the user answers a
        repair slot prompt. Without this branch, a short follow-up message
        (e.g. an appointment time like ``2026-08-31T16:00`` or the deferral
        phrase ``稍后协商``) is routed to the LLM intent classifier on its
        own and gets mis-classified as UNCERTAIN, wiping out the repair
        context — even though the bounded-analysis path has a
        ``state.intent in _INTENT_DOMAINS`` fallback that the semantic
        gateway path does not.

        By short-circuiting straight to a ``repair_create`` step with the
        carried slots, the capability layer re-prompts for any still-missing
        required field and the deferral/real-time paths reach confirmation
        as designed. ``state.intent`` only becomes ``"REPAIR"`` via the
        runner's continuation policy, so this branch is a no-op for fresh
        requests where the LLM should still classify intent.
        """
        if state.intent != "REPAIR" or state.slots.get("action") not in {"create", "submit"}:
            return None
        parameters: dict[str, Any] = {}
        for key in ("description", "location", "urgency", "appointment_at"):
            if key in state.slots:
                parameters[key] = state.slots[key]
        parameters.setdefault("urgency", "NORMAL")
        step = PlanStepProposal(
            step_id="repair-create-continuation-v2",
            goal="分步填写并提交报修工单",
            domain="repair",
            specialist=SpecialistName.REPAIR.value,
            capability="repair_create",
            parameters=parameters,
        )
        return PlanProposal(
            ObjectiveClassification.SINGLE_DOMAIN.value,
            (step,),
            "deterministic-repair-continuation",
        )

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

    def _bounded_analysis_proposal(
        self, text: str, state: AgentState, runtime: RuntimeContext
    ) -> PlanProposal:
        analysis = self._bounded_analysis(text, state, runtime)
        if analysis is None:
            return PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        if analysis.intent == "UNCERTAIN" and state.intent in _INTENT_DOMAINS:
            analysis = ModelAnalysis(
                intent=state.intent,
                confidence=0.9,
                slots=dict(state.slots),
                provider="bounded_context",
            )
        if analysis.intent == "GENERAL_HELP":
            return PlanProposal(ObjectiveClassification.GENERAL_HELP.value, (), analysis.provider)
        action = str(analysis.slots.get("action") or state.slots.get("action") or "")
        if not action and analysis.intent == "BILLING":
            action = str(analysis.slots.get("query_type") or state.slots.get("query_type") or "")
        if analysis.confidence < 0.9 or not action:
            return PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        step = self._bounded_step(analysis.intent, action, {**analysis.slots, **state.slots})
        if step is None:
            return PlanProposal(ObjectiveClassification.UNCERTAIN.value, (), "safe-fallback")
        return PlanProposal(ObjectiveClassification.SINGLE_DOMAIN.value, (step,), analysis.provider)

    def _bounded_analysis(
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
        parameters = {
            **{
                key: value
                for key, value in semantic_slots.items()
                if key not in _TRUSTED_KEYS and not key.startswith("_")
            },
            **proposal.parameters,
        }
        if proposal.capability == "repair_create":
            user_text = str(semantic_slots.get("user_text") or "")
            explicit = deterministic_repair_slots(user_text)
            for key in ("location", "description"):
                if explicit.get(key):
                    parameters[key] = explicit[key]
        return PlanStep(
            step_id=proposal.step_id,
            domain=proposal.domain,
            specialist=SpecialistName(proposal.specialist),
            goal=proposal.goal,
            dependencies=proposal.dependencies,
            capability=proposal.capability,
            parameters=parameters,
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
    def _bounded_step(intent: str, action: str, slots: dict[str, Any]) -> PlanStepProposal | None:
        domain = intent.lower()
        capability = _bounded_capability(domain, action, slots)
        if domain not in _DOMAIN_SPECIALIST or capability is None:
            return None
        parameters = {key: value for key, value in slots.items() if key not in _TRUSTED_KEYS}
        return PlanStepProposal(
            step_id=f"{domain}-v2",
            goal="执行明确的单领域请求",
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
_INTENT_DOMAINS = frozenset(intent.upper() for intent in _DOMAIN_SPECIALIST)


def _bounded_capability(domain: str, action: str, slots: dict[str, Any]) -> str | None:
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
            "knowledge": "community_knowledge_search",
            "get": "announcement_get",
            "draft": "announcement_draft",
            "revise": "announcement_revise",
            "create": "announcement_create_draft",
            "publish": "announce_publish",
            "schedule": "announcement_schedule_publish",
        },
        "inspection": {
            "list": "inspection_list",
            "query": "inspection_list",
            "get_task": "inspection_get_task",
            "get_event": "inspection_get_event",
            "create": "inspection_create",
            "report_event": "security_event_create",
        },
    }
    return mappings.get(domain, {}).get(action)
