"""Public response presentation for the legacy Plan fallback."""

from __future__ import annotations

from typing import Any

from property_agent.agent.orchestration import GoalOutcome, ObjectiveClassification


def synthesize_legacy_plan(state: Any) -> str:
    """Render a legacy Plan outcome without leaking orchestration internals."""
    if state.messages and state.messages[-1].get("content") == "已取消，未执行任何操作。":
        return "已取消，未执行任何操作。"
    plan = state.plan
    if plan.objective_classification == ObjectiveClassification.GENERAL_HELP:
        return "我可以协助报修、账单、公告和巡检安防事务。涉及写入时会逐项请您确认。"
    if plan.objective_classification == ObjectiveClassification.UNCERTAIN:
        return "请说明您要查询或办理的是报修、账单、公告还是巡检安防事项。"
    labels = {
        GoalOutcome.COMPLETED: "已完成",
        GoalOutcome.CONDITION_NOT_MET: "条件未满足，未执行",
        GoalOutcome.PENDING_CONFIRMATION: "待确认",
        GoalOutcome.NEEDS_CLARIFICATION: "需补充信息",
        GoalOutcome.FAILED: "失败",
        GoalOutcome.HANDOVER: "需人工处理",
    }
    parts = []
    results = {item.step_id: item for item in state.specialist_results}
    for step in plan.steps:
        outcome = state.goal_outcomes.get(step.step_id)
        if outcome is None:
            continue
        result = results.get(step.step_id)
        public_message = result.public_message if result is not None else ""
        parts.append(
            f"{public_message}（{labels[outcome]}）"
            if public_message
            else f"{step.goal}：{labels[outcome]}"
        )
    return "；".join(parts) if parts else "当前任务已停止，未执行新操作。"
