"""Stable presentation for an exact pending write."""

from typing import Any

from property_agent.agent.state import GraphState


def confirmation_envelope(state: GraphState) -> dict[str, Any]:
    """Build the stable public confirmation projection from durable state."""
    pending = dict(state.pending_action or {})
    return {
        "type": "confirmation",
        "summary": f"确认执行操作：{pending.get('tool')}",
        "action": pending,
        "action_hash": pending.get("params_hash"),
    }
