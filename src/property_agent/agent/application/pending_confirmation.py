"""Presentation and supported-revision rules for a pending write."""

from typing import Any

from property_agent.agent.announcement_actions import (
    AnnouncementAgentAction,
    resolve_announcement_followup,
)
from property_agent.agent.application.runner_signals import (
    explicit_inspection_corrections,
    explicit_repair_corrections,
)
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import AnnouncementDraftingState


def confirmation_envelope(state: GraphState) -> dict[str, Any]:
    """Build the stable public confirmation projection from durable state."""
    pending = dict(state.pending_action or {})
    return {
        "type": "confirmation",
        "summary": f"确认执行操作：{pending.get('tool')}",
        "action": pending,
        "action_hash": pending.get("params_hash"),
    }


def is_supported_pending_revision(user_text: str, state: GraphState) -> bool:
    """Recognize only deterministic revision forms already supported by v1."""
    if explicit_repair_corrections(user_text):
        return True
    if explicit_inspection_corrections(user_text, state):
        return True
    if isinstance(state.domain, AnnouncementDraftingState):
        followup = resolve_announcement_followup(user_text, has_active_draft=True)
        return followup.action is AnnouncementAgentAction.REVISE
    return False
