"""Structured planner implementations for the bounded read-only loop."""

from __future__ import annotations

from typing import Any

from property_agent.agent.read_contracts import PlannerAction, PlannerDecision


class GatewayReadPlanner:
    """Use a model planner when available and keep a deterministic fail-open path."""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    def plan_read(self, **context: Any) -> PlannerDecision:
        method = getattr(self._gateway, "plan_read", None)
        if method is None:
            return self.deterministic_plan_read(**context)
        decision = method(**context)
        if decision.action == PlannerAction.FINAL:
            required = self.deterministic_plan_read(**context)
            if required.action == PlannerAction.CALL_TOOL:
                return required
        return decision

    def deterministic_plan_read(
        self,
        *,
        question: str,
        intent: str,
        slots: dict[str, Any],
        trusted_context: dict[str, Any],
        observations: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> PlannerDecision:
        del trusted_context, tools
        called = {item.get("tool") for item in observations}
        if "get_current_context" not in called:
            return PlannerDecision(
                PlannerAction.CALL_TOOL,
                "get_current_context",
                reason_code="NEED_TRUSTED_SCOPE",
            )
        if intent == "ANNOUNCEMENT":
            if slots.get("target_date") and "get_business_date" not in called:
                return PlannerDecision(
                    PlannerAction.CALL_TOOL,
                    "get_business_date",
                    reason_code="VERIFY_BUSINESS_DATE",
                )
            if "search_announcements" not in called:
                arguments = {
                    key: slots[key]
                    for key in ("topic", "target_date", "statuses", "limit")
                    if slots.get(key) is not None
                }
                return PlannerDecision(
                    PlannerAction.CALL_TOOL,
                    "search_announcements",
                    arguments,
                    "NEED_ANNOUNCEMENT_FACTS",
                )
        elif intent == "BILLING":
            tool = "get_bill" if slots.get("bill_id") else "list_bills"
            if tool not in called:
                arguments = {
                    key: slots[key]
                    for key in ("bill_id", "period", "fee_type")
                    if slots.get(key) is not None
                }
                return PlannerDecision(
                    PlannerAction.CALL_TOOL,
                    tool,
                    arguments,
                    "NEED_BILLING_FACTS",
                )
        elif intent == "REPAIR":
            tool = "get_work_order" if slots.get("work_order_id") else "list_work_orders"
            if tool not in called:
                arguments = (
                    {"work_order_id": slots["work_order_id"]} if slots.get("work_order_id") else {}
                )
                return PlannerDecision(
                    PlannerAction.CALL_TOOL,
                    tool,
                    arguments,
                    "NEED_WORK_ORDER_FACTS",
                )
        elif intent == "INSPECTION":
            target = str(slots.get("target") or "task").lower()
            if target == "event" or slots.get("event_id"):
                tool = "get_security_event" if slots.get("event_id") else "list_security_events"
                if tool not in called:
                    arguments = (
                        {"event_id": str(slots["event_id"])}
                        if slots.get("event_id")
                        else {
                            key: slots[key]
                            for key in ("statuses", "risk_levels", "assigned_to_me", "limit")
                            if slots.get(key) is not None
                        }
                    )
                    return PlannerDecision(
                        PlannerAction.CALL_TOOL, tool, arguments, "NEED_SECURITY_EVENT_FACTS"
                    )
            else:
                tool = "get_inspection_task" if slots.get("task_id") else "list_inspection_tasks"
                if tool not in called:
                    arguments = (
                        {"task_id": str(slots["task_id"])}
                        if slots.get("task_id")
                        else {
                            key: slots[key]
                            for key in ("statuses", "assigned_to_me", "limit")
                            if slots.get(key) is not None
                        }
                    )
                    return PlannerDecision(
                        PlannerAction.CALL_TOOL, tool, arguments, "NEED_INSPECTION_TASK_FACTS"
                    )
        elif intent == "GENERAL_HELP" and "search_community_knowledge" not in called:
            return PlannerDecision(
                PlannerAction.CALL_TOOL,
                "search_community_knowledge",
                {"query": str(slots.get("user_text") or question)[:128]},
                "NEED_PUBLISHED_COMMUNITY_FACTS",
            )
        return PlannerDecision(
            PlannerAction.FINAL,
            reason_code="ANSWER_READY",
            answer_goal=f"SUMMARIZE_{intent}",
        )
