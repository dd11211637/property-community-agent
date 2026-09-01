"""Fallback orchestration and deterministic guards for model output."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from property_agent.agent.deterministic_gateway import (
    DeterministicModelGateway,
    deterministic_inspection_slots,
    deterministic_repair_slots,
)
from property_agent.agent.model_contracts import ModelAnalysis, ModelGateway, ModelGatewayError
from property_agent.agent.planning_contracts import PlanProposal, RelevanceJudgment
from property_agent.agent.policies import Intent
from property_agent.repair.domain.classification import classify_repair_category


def _with_slots(result: ModelAnalysis, slots: dict[str, Any]) -> ModelAnalysis:
    return ModelAnalysis(
        intent=result.intent,
        confidence=result.confidence,
        slots=slots,
        provider=result.provider,
        degraded=result.degraded,
    )


def _guard_billing_slots(
    result: ModelAnalysis, deterministic_slots: dict[str, Any] | None
) -> ModelAnalysis:
    slots = dict(result.slots)
    if deterministic_slots and deterministic_slots.get("period"):
        slots["period"] = deterministic_slots["period"]
        slots["query_type"] = "list"
    return _with_slots(result, slots)


def _guard_announcement_slots(
    result: ModelAnalysis, deterministic_slots: dict[str, Any] | None
) -> ModelAnalysis:
    slots = dict(result.slots)
    for key in ("action", "audience", "topic", "target_date", "scheduled_at"):
        if deterministic_slots and key in deterministic_slots:
            slots[key] = deterministic_slots[key]
    return _with_slots(result, slots)


def _guard_inspection_slots(
    text: str,
    result: ModelAnalysis,
    deterministic_slots: dict[str, Any] | None,
) -> ModelAnalysis:
    slots = dict(result.slots)
    guarded = deterministic_slots or deterministic_inspection_slots(text)
    for key in ("action", "target", "event_type"):
        if guarded.get(key):
            slots[key] = guarded[key]
    if guarded.get("action") == "create":
        for key in ("title", "description", "point"):
            if guarded.get(key):
                slots[key] = guarded[key]
    if guarded.get("risk_level") == "HIGH_RISK":
        slots["risk_level"] = "HIGH_RISK"
    return _with_slots(result, slots)


def _guard_repair_slots(
    text: str,
    result: ModelAnalysis,
    deterministic_slots: dict[str, Any] | None,
) -> ModelAnalysis:
    slots = dict(result.slots)
    guarded = deterministic_slots or deterministic_repair_slots(text)
    for key in ("action", "work_order_id", "location", "description"):
        if guarded.get(key):
            slots[key] = guarded[key]
    query_markers = (
        "查询工单",
        "查看工单",
        "查工单",
        "工单进度",
        "报修进度",
        "维修进度",
        "报修记录",
        "维修记录",
    )
    create_markers = ("我要报修", "需要报修", "发起报修", "提交报修")
    if any(marker in text for marker in query_markers):
        slots["action"] = "query"
    elif any(marker in text for marker in create_markers):
        slots["action"] = "create"
        slots.pop("work_order_id", None)
    if slots.get("action") == "create":
        model_location = slots.get("location")
        if model_location and not guarded.get("location") and str(model_location) not in text:
            slots.pop("location", None)
        if not slots.get("location"):
            locations = ("厨房", "卫生间", "客厅", "卧室", "阳台", "玄关", "楼道", "车库")
            location = next((value for value in locations if value in text), None)
            if location:
                slots["location"] = location
        damage_cues = ("坏了", "损坏", "漏水", "漏电", "堵塞", "停电", "跳闸", "故障", "破损")
        if not slots.get("description") and any(cue in text for cue in damage_cues):
            slots["description"] = text.strip()
        if slots.get("description"):
            slots["category"] = classify_repair_category(str(slots["description"])).value
    work_order_id = slots.get("work_order_id")
    if work_order_id is not None and not re.fullmatch(
        r"WX-[A-Z0-9]+(?:-[A-Z0-9]+)*", str(work_order_id).strip().upper()
    ):
        slots.pop("work_order_id", None)
    return _with_slots(result, slots)


def _apply_deterministic_slot_guards(
    text: str,
    result: ModelAnalysis,
    deterministic_slots: dict[str, Any] | None = None,
) -> ModelAnalysis:
    if result.intent == Intent.BILLING.value:
        return _guard_billing_slots(result, deterministic_slots)
    if result.intent == Intent.ANNOUNCEMENT.value:
        return _guard_announcement_slots(result, deterministic_slots)
    if result.intent == Intent.INSPECTION.value:
        return _guard_inspection_slots(text, result, deterministic_slots)
    if result.intent == Intent.REPAIR.value:
        return _guard_repair_slots(text, result, deterministic_slots)
    return result


class FallbackModelGateway:
    """Use a deterministic gateway after a controlled primary-model failure."""

    def __init__(
        self,
        primary: ModelGateway,
        fallback: ModelGateway | None = None,
        *,
        observe: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or DeterministicModelGateway()
        self._observe = observe or (lambda _event, _fields: None)

    def ready(self) -> bool:
        return self._primary.ready() or self._fallback.ready()

    def analyze(self, text: str) -> ModelAnalysis:
        return self.analyze_with_context(text, history=[], trusted_context={})

    def analyze_with_context(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
    ) -> ModelAnalysis:
        fallback_contextual = getattr(self._fallback, "analyze_with_context", None)
        deterministic = (
            fallback_contextual(text, history=history, trusted_context=trusted_context)
            if fallback_contextual is not None
            else self._fallback.analyze(text)
        )
        try:
            contextual = getattr(self._primary, "analyze_with_context", None)
            primary = (
                contextual(text, history=history, trusted_context=trusted_context)
                if contextual is not None
                else self._primary.analyze(text)
            )
        except ModelGatewayError:
            result = _apply_deterministic_slot_guards(
                text,
                ModelAnalysis(
                    intent=deterministic.intent,
                    confidence=deterministic.confidence,
                    slots=deterministic.slots,
                    provider=deterministic.provider,
                    degraded=True,
                ),
                deterministic.slots,
            )
            self._fallback_outcome("analyze_with_context", "success")
            return result

        # Explicit domain words are authoritative routing signals.  The model still
        # contributes non-authoritative slots, but a syntactically valid UNCERTAIN or
        # conflicting response must not break obvious requests such as "查账单".
        if (
            deterministic.intent != Intent.UNCERTAIN.value
            and primary.intent != deterministic.intent
        ):
            return _apply_deterministic_slot_guards(
                text,
                ModelAnalysis(
                    intent=deterministic.intent,
                    confidence=max(primary.confidence, deterministic.confidence),
                    slots=primary.slots,
                    provider=f"{primary.provider}+keyword_guard",
                    degraded=True,
                ),
                deterministic.slots,
            )
        if deterministic.intent != Intent.UNCERTAIN.value:
            primary = ModelAnalysis(
                intent=primary.intent,
                confidence=max(primary.confidence, deterministic.confidence),
                slots=primary.slots,
                provider=primary.provider,
                degraded=primary.degraded,
            )
        return _apply_deterministic_slot_guards(text, primary, deterministic.slots)

    def propose_plan(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
        memory_context: dict[str, Any] | None = None,
    ) -> PlanProposal:
        """Use only the semantic provider; never emulate complex planning lexically."""
        method = getattr(self._primary, "propose_plan", None)
        if method is None:
            raise ModelGatewayError("Primary model does not support semantic planning")
        return method(
            text,
            history=history,
            trusted_context=trusted_context,
            memory_context=memory_context or {},
        )

    def extract_candidates(self, **kwargs: Any) -> tuple[Any, ...]:
        method = getattr(self._primary, "extract_candidates", None)
        if method is None:
            raise ModelGatewayError("Primary model does not support memory extraction")
        return method(**kwargs)

    def judge_relevance(
        self,
        *,
        semantic_goal: str,
        evidence: dict[str, Any],
    ) -> RelevanceJudgment:
        method = getattr(self._primary, "judge_relevance", None)
        if method is None:
            raise ModelGatewayError("Primary model does not support relevance judgment")
        return method(semantic_goal=semantic_goal, evidence=evidence)

    def resolve_goal(self, context: dict[str, Any]):
        method = getattr(self._primary, "resolve_goal", None)
        if method is None:
            raise ModelGatewayError("Primary model does not support Goal resolution")
        return method(context)

    def react_decide(self, context: dict[str, Any]):
        method = getattr(self._primary, "react_decide", None)
        if method is None:
            raise ModelGatewayError("Primary model does not support ReAct decisions")
        return method(context)

    def classify_intent(self, text: str) -> tuple[str, float]:
        result = self.analyze(text)
        return result.intent, result.confidence

    def extract_slots(self, text: str, intent: str) -> dict[str, Any]:
        return self.analyze(text).slots

    def draft_announcement(self, *, topic: str, audience: Any, requirements: str) -> dict[str, str]:
        try:
            return self._primary.draft_announcement(
                topic=topic, audience=audience, requirements=requirements
            )
        except (AttributeError, ModelGatewayError):
            return self._use_fallback(
                "draft_announcement",
                lambda: self._fallback.draft_announcement(
                    topic=topic, audience=audience, requirements=requirements
                ),
            )

    def revise_announcement(
        self, *, draft: dict[str, str], audience: Any, instruction: str
    ) -> dict[str, str]:
        try:
            return self._primary.revise_announcement(
                draft=draft, audience=audience, instruction=instruction
            )
        except (AttributeError, ModelGatewayError):
            return self._use_fallback(
                "revise_announcement",
                lambda: self._fallback.revise_announcement(
                    draft=draft, audience=audience, instruction=instruction
                ),
            )

    def plan_read(self, **context: Any):
        method = getattr(self._primary, "plan_read", None)
        if method is None:
            raise ModelGatewayError("Primary model does not support read planning")
        return method(**context)

    def _use_fallback(self, operation: str, invoke: Callable[[], Any]) -> Any:
        try:
            result = invoke()
        except Exception:
            self._fallback_outcome(operation, "failure")
            raise
        self._fallback_outcome(operation, "success")
        return result

    def _fallback_outcome(self, operation: str, outcome: str) -> None:
        self._observe(
            "model_fallback",
            {
                "provider": type(self._fallback).__name__,
                "operation": operation,
                "outcome": outcome,
            },
        )
