"""Fail-open transcript persistence boundary."""

import logging
from collections.abc import Callable

from property_agent.agent.application.conversation_service import AgentContext
from property_agent.agent.application.runner import AgentTurn
from property_agent.agent.state import GraphState

logger = logging.getLogger(__name__)
TurnRecorder = Callable[[AgentContext, GraphState, str, str], None]


def record_turn(
    recorder: TurnRecorder | None,
    context: AgentContext,
    turn: AgentTurn,
    user_text: str,
) -> None:
    """Do not replay a successful business write when transcript storage fails."""
    if recorder is None:
        return
    try:
        recorder(context, turn.state, user_text, turn.reply)
    except Exception:
        logger.exception(
            "agent_transcript_persist_failed",
            extra={"conversation_id": turn.state.conversation_id},
        )
