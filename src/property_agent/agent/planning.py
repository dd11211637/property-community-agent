"""Bounded deterministic planning with governed ModelGateway classification input."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from property_agent.agent.deterministic_gateway import DeterministicModelGateway
from property_agent.agent.model_contracts import ModelGateway, ModelGatewayError
from property_agent.agent.orchestration import (
    ObjectiveClassification,
    Plan,
    PlanStep,
    PlanValidator,
    SpecialistName,
)
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.state import AgentState

_DOMAIN_SPECIALIST = {
    "repair": SpecialistName.REPAIR,
    "billing": SpecialistName.BILLING,
    "announcement": SpecialistName.ANNOUNCEMENT,
    "inspection": SpecialistName.INSPECTION,
}
_DOMAIN_CUES = {
    "repair": ("报修", "维修", "工单", "漏水", "漏电", "故障", "坏了"),
    "billing": ("账单", "物业费", "欠费", "缴费", "费用", "收费"),
    "announcement": ("公告", "通知", "通告", "发布", "告示"),
    "inspection": ("巡检", "安防", "安保", "异常", "隐患", "事件"),
}


class SupervisorPlanner:
    """Construct small executable plans; model output remains a proposal only."""

    def __init__(self, gateway: ModelGateway, *, validator: PlanValidator | None = None) -> None:
        self._gateway = gateway
        self._fallback = DeterministicModelGateway()
        self._validator = validator or PlanValidator()

    def create_plan(self, state: AgentState, runtime: RuntimeContext) -> Plan:
        text = str(state.slots.get("user_text") or "").strip()
        analysis = self._analyze(text, state, runtime)
        domains = self._ordered_domains(text)
        if not domains and analysis.intent in {"REPAIR", "BILLING", "ANNOUNCEMENT", "INSPECTION"}:
            domains = [analysis.intent.lower()]
        classification = self._classification(domains, analysis.intent)
        steps = self._steps(text, domains, state.slots)
        plan = Plan(
            plan_id=f"plan-{uuid4()}",
            objective=text or "需要澄清的用户目标",
            objective_classification=classification,
            steps=tuple(steps),
            current_step_id=steps[0].step_id if steps else None,
        )
        return self._validator.validate(plan, global_intent=analysis.intent)

    def _analyze(self, text: str, state: AgentState, runtime: RuntimeContext):
        trusted = {
            "business_date": state.trusted_context.get("business_date"),
            "has_current_house": runtime.current_house_id is not None,
        }
        try:
            method = getattr(self._gateway, "analyze_with_context", None)
            if method is not None:
                analysis = method(text, history=list(state.messages[-12:]), trusted_context=trusted)
            else:
                analysis = self._gateway.analyze(text)
        except (AttributeError, ModelGatewayError):
            analysis = None
        if not isinstance(getattr(analysis, "intent", None), str):
            analysis = self._fallback.analyze_with_context(
                text, history=list(state.messages[-12:]), trusted_context=trusted
            )
        return analysis

    @staticmethod
    def _ordered_domains(text: str) -> list[str]:
        positions = []
        for domain, cues in _DOMAIN_CUES.items():
            found = [text.find(cue) for cue in cues if cue in text]
            if found:
                positions.append((min(found), domain))
        domains = [domain for _, domain in sorted(positions)]
        inspection_frames_fault = "inspection" in domains and "巡检" in text
        explicit_repair = any(cue in text for cue in ("报修", "工单", "维修"))
        if inspection_frames_fault and not explicit_repair:
            domains = [domain for domain in domains if domain != "repair"]
        return domains

    @staticmethod
    def _classification(domains: list[str], intent: str) -> ObjectiveClassification:
        if len(domains) > 1:
            return ObjectiveClassification.MULTI_DOMAIN
        if domains:
            return ObjectiveClassification.SINGLE_DOMAIN
        if intent == "GENERAL_HELP":
            return ObjectiveClassification.GENERAL_HELP
        return ObjectiveClassification.UNCERTAIN

    def _steps(
        self, text: str, domains: list[str], semantic_slots: dict[str, Any]
    ) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for domain in domains:
            additions = self._domain_steps(
                domain,
                text,
                steps[-1].step_id if steps else None,
                semantic_slots,
            )
            steps.extend(additions)
        return self._renumber_duplicates(steps)

    def _domain_steps(
        self,
        domain: str,
        text: str,
        prior_step: str | None,
        semantic_slots: dict[str, Any],
    ) -> list[PlanStep]:
        dependency = (prior_step,) if prior_step else ()
        if domain == "repair":
            return self._repair_steps(text, dependency, semantic_slots)
        if domain == "billing":
            return [self._billing_step(text, dependency)]
        if domain == "inspection":
            return [self._inspection_step(text, dependency, semantic_slots)]
        return [self._announcement_step(text, dependency, semantic_slots)]

    def _repair_steps(
        self,
        text: str,
        dependency: tuple[str, ...],
        semantic_slots: dict[str, Any],
    ) -> list[PlanStep]:
        slots = {**self._fallback.extract_slots(text, "REPAIR"), **semantic_slots}
        conditional_create = any(cue in text for cue in ("如果没有", "没有的话", "没有就"))
        if conditional_create:
            read = self._step(
                "repair-read", "repair", "repair_list", "查找等价活跃报修", dependency
            )
            create = self._step(
                "repair-create",
                "repair",
                "repair_create",
                "不存在等价工单时提交报修",
                (read.step_id,),
                self._repair_create_parameters(text, slots),
                "if_no_equivalent_active_repair",
            )
            return [read, create]
        if self._is_write(
            text, ("提交", "创建", "新建", "帮我报", "报一个", "我要报修", "帮我处理")
        ):
            return [
                self._step(
                    "repair-create",
                    "repair",
                    "repair_create",
                    "提交报修",
                    dependency,
                    self._repair_create_parameters(text, slots),
                )
            ]
        capability = "repair_get" if slots.get("work_order_id") else "repair_list"
        parameters = (
            {"work_order_id": str(slots["work_order_id"])}
            if slots.get("work_order_id")
            else {"statuses": tuple(slots.get("statuses") or ()), "limit": 20}
        )
        return [
            self._step("repair-read", "repair", capability, "查询报修进度", dependency, parameters)
        ]

    def _billing_step(self, text: str, dependency: tuple[str, ...]) -> PlanStep:
        slots = self._fallback.extract_slots(text, "BILLING")
        if self._is_write(text, ("咨询", "投诉", "反馈", "提交")):
            parameters = {
                "subject": str(slots.get("subject") or "账单咨询"),
                "description": str(slots.get("description") or text),
                "bill_id": slots.get("bill_id"),
            }
            return self._step(
                "billing-consult",
                "billing",
                "billing_consult",
                "提交账单咨询",
                dependency,
                parameters,
            )
        parameters = {
            "query_type": str(slots.get("query_type") or "list"),
            "period": slots.get("period"),
            "fee_type": slots.get("fee_type"),
            "bill_id": slots.get("bill_id"),
        }
        return self._step(
            "billing-read", "billing", "billing_query", "查询账单事实", dependency, parameters
        )

    def _inspection_step(
        self, text: str, dependency: tuple[str, ...], semantic_slots: dict[str, Any]
    ) -> PlanStep:
        slots = {**self._fallback.extract_slots(text, "INSPECTION"), **semantic_slots}
        action = str(slots.get("action") or "")
        capabilities = {
            "get_task": "inspection_get_task",
            "get_event": "inspection_get_event",
            "create": "inspection_create",
            "start_task": "inspection_start_task",
            "add_record": "inspection_add_record",
            "submit_records": "inspection_submit_records",
            "ai_suggest": "inspection_ai_suggest",
            "report_event": "security_event_create",
            "submit_disposal": "security_event_submit_disposal",
            "close_high_risk": "close_high_risk_event",
        }
        if action in capabilities:
            return self._step(
                "inspection-action",
                "inspection",
                capabilities[action],
                "执行巡检或安防目标",
                dependency,
                dict(slots),
            )
        parameters = {
            "target": "event" if any(cue in text for cue in ("事件", "安防")) else "task",
            "statuses": tuple(slots.get("statuses") or ()),
            "risk_levels": tuple(slots.get("risk_levels") or ()),
            "assigned_to_me": bool(slots.get("assigned_to_me", False)),
            "limit": int(slots.get("limit") or 20),
        }
        return self._step(
            "inspection-read",
            "inspection",
            "inspection_list",
            "查询巡检或事件事实",
            dependency,
            parameters,
        )

    def _announcement_step(
        self, text: str, dependency: tuple[str, ...], semantic_slots: dict[str, Any]
    ) -> PlanStep:
        action = str(semantic_slots.get("action") or "")
        actions = {
            "get": "announcement_get",
            "draft": "announcement_draft",
            "revise": "announcement_revise",
            "create": "announcement_create_draft",
            "publish": "announce_publish",
            "schedule": "announcement_schedule_publish",
        }
        if action in actions:
            return self._step(
                "announcement-action",
                "announcement",
                actions[action],
                "执行公告目标",
                dependency,
                dict(semantic_slots),
            )
        conditional = bool(dependency) and any(cue in text for cue in ("如果", "确实", "真的"))
        if any(cue in text for cue in ("准备", "起草", "写一份", "公告")):
            parameters = {"topic": text[:200], "audience": {}, "requirements": text[:4000]}
            return self._step(
                "announcement-draft",
                "announcement",
                "announcement_draft",
                "基于已核验事实准备公告",
                dependency,
                parameters,
                "if_relevant_inspection_issue" if conditional else None,
            )
        return self._step(
            "announcement-read",
            "announcement",
            "announcement_list",
            "查询公告",
            dependency,
            {"statuses": (), "limit": 20},
        )

    @staticmethod
    def _repair_create_parameters(text: str, slots: dict[str, Any]) -> dict[str, Any]:
        return {
            "description": str(slots.get("description") or text),
            "location": str(slots.get("location") or ""),
            "urgency": str(slots.get("urgency") or "NORMAL"),
        }

    @staticmethod
    def _is_write(text: str, cues: Iterable[str]) -> bool:
        return any(cue in text for cue in cues)

    @staticmethod
    def _step(
        step_id: str,
        domain: str,
        capability: str,
        goal: str,
        dependencies: tuple[str, ...],
        parameters: dict[str, Any] | None = None,
        condition: str | None = None,
    ) -> PlanStep:
        return PlanStep(
            step_id=step_id,
            domain=domain,
            specialist=_DOMAIN_SPECIALIST[domain],
            goal=goal,
            dependencies=dependencies,
            capability=capability,
            parameters=parameters or {},
            condition=condition,
        )

    @staticmethod
    def _renumber_duplicates(steps: list[PlanStep]) -> list[PlanStep]:
        seen: dict[str, int] = {}
        result = []
        for step in steps:
            count = seen.get(step.step_id, 0)
            seen[step.step_id] = count + 1
            if count:
                step = PlanStep.from_dict(
                    {**step.to_dict(), "step_id": f"{step.step_id}-{count + 1}"}
                )
            result.append(step)
        return result
