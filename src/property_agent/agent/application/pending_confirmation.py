"""Stable presentation for an exact pending write."""

from typing import Any

from property_agent.agent.state import GraphState
from property_agent.repair.domain.classification import classify_repair_category


def confirmation_envelope(state: GraphState) -> dict[str, Any]:
    """Build the stable public confirmation projection from durable state."""
    pending = dict(state.pending_action or {})
    params = dict(pending.get("params") or {})
    if pending.get("tool") == "repair_create" and "description" in params:
        params["category"] = classify_repair_category(str(params["description"])).value
    return {
        "type": "confirmation",
        "summary": f"确认执行操作：{pending.get('tool')}",
        "action": {**pending, "params": params},
        "action_hash": pending.get("params_hash"),
    }
