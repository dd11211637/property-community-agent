"""Canonical accepted-turn outcome projection shared by v1 and v2 lifecycles."""

from property_agent.agent.memory_contracts import AcceptedTurnOutcome
from property_agent.agent.orchestration import PlanStatus
from property_agent.agent.state import AgentState


def accepted_turn_outcome(
    state: AgentState, *, done: bool, cancelled: bool = False
) -> AcceptedTurnOutcome:
    if cancelled:
        return AcceptedTurnOutcome.CANCELLED
    if not done:
        return AcceptedTurnOutcome.PENDING
    if state.plan is not None:
        return {
            PlanStatus.COMPLETED: AcceptedTurnOutcome.COMPLETED,
            PlanStatus.WAITING_CONFIRMATION: AcceptedTurnOutcome.PENDING,
            PlanStatus.FAILED: AcceptedTurnOutcome.FAILED,
            PlanStatus.PARTIAL: AcceptedTurnOutcome.PARTIAL,
            PlanStatus.NEEDS_CLARIFICATION: AcceptedTurnOutcome.PARTIAL,
            PlanStatus.HANDOVER: AcceptedTurnOutcome.PARTIAL,
        }.get(state.plan.status, AcceptedTurnOutcome.PARTIAL)
    if state.error or (state.tool_result and state.tool_result.get("ok") is False):
        return AcceptedTurnOutcome.FAILED
    if state.handover_required or state.missing_slots or state.requested_slot:
        return AcceptedTurnOutcome.PARTIAL
    return AcceptedTurnOutcome.COMPLETED
