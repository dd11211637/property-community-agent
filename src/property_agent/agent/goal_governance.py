"""Supervisor governance for Plan-free active Goals."""

from __future__ import annotations

from typing import Any

from property_agent.agent.application.goal_resolution import GoalResolutionError, GoalResolver
from property_agent.agent.react_contracts import GoalStatus


class GoalSupervisorGovernance:
    """Own Goal resolution, direct-mode eligibility, limits, and presentation."""

    def __init__(self, gateway: Any | None) -> None:
        self._resolver = GoalResolver(gateway) if gateway is not None else None

    def resolve(self, state: Any, runtime: Any, planner: Any) -> bool:
        if not state.goal_resolution_pending or self._resolver is None:
            return False
        try:
            self._resolver.resolve(state, runtime)
            return False
        except GoalResolutionError:
            state.goal_resolution_pending = False
            state.goal_resolution_kind = "LEGACY_FALLBACK"
            state.plan = planner.create_plan(state, runtime)
            state.legacy_plan = state.plan
            return True

    @staticmethod
    def is_direct(state: Any, runtime: Any) -> bool:
        goal = state.active_goal
        return bool(
            goal is not None
            and state.plan is None
            and goal.domain in runtime.execution_policy.react_domains
        )

    @staticmethod
    def prepare_direct(
        state: Any,
        runtime: Any,
        now: Any,
        *,
        budget_expired: Any,
        limit_reached: Any,
        increment: Any,
    ) -> Any:
        if budget_expired(state, now):
            return GoalSupervisorGovernance.fail(state, "EXECUTION_DEADLINE_EXCEEDED")
        if limit_reached(state, runtime, "supervisor_steps", "max_supervisor_steps"):
            return GoalSupervisorGovernance.fail(state, "MAX_SUPERVISOR_STEPS_EXCEEDED")
        increment(state, supervisor_steps=1)
        return state

    @staticmethod
    def fail(state: Any, reason: str) -> Any:
        if state.active_goal is not None:
            state.active_goal.status = GoalStatus.HANDOVER
            state.active_goal.handover = True
        state.handover_required = True
        state.error = reason
        return state

    @staticmethod
    def synthesize(state: Any) -> str:
        goal = state.active_goal
        if state.goal_resolution_kind == "GENERAL_HELP":
            return "我可以协助报修、账单、公告和巡检安防事务。涉及写入时会请您确认。"
        if state.goal_resolution_kind == "UNCERTAIN":
            return state.goal_resolution_message or "请再说明一下您希望完成的社区事务。"
        if goal is None:
            return state.goal_resolution_message or "当前没有进行中的任务。"
        if goal.status is GoalStatus.CANCELLED:
            return "已取消当前任务，未执行新的写操作。"
        if goal.status is GoalStatus.NEEDS_CLARIFICATION:
            return goal.last_public_message or "请补充完成当前任务所需的信息。"
        if goal.status is GoalStatus.HANDOVER:
            return goal.last_public_message or "该任务需要转由人工继续处理。"
        if goal.status in {GoalStatus.COMPLETED, GoalStatus.PARTIAL}:
            return goal.last_public_message or "当前任务已处理完成。"
        return goal.last_public_message or "正在继续处理当前任务。"
