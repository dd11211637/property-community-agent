"""User-facing slot prompts for guided business conversations.

The graph keeps validation deterministic, while this module translates an active
business field into Chinese questions and safe UI choices.  Values remain business
inputs supplied by the user; no option is treated as a verified business fact.
"""

from __future__ import annotations

from typing import Any

from property_agent.agent.state import GraphState

_REPAIR_FIELD_LABELS = {
    "description": "故障现象",
    "location": "发生地点",
}


def _option(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def _repair_completed(state: GraphState) -> list[dict[str, str]]:
    completed: list[dict[str, str]] = []
    for field in ("description", "location"):
        value = state.slots.get(field)
        if value is None or not str(value).strip():
            continue
        display = str(value).strip()
        completed.append(
            {
                "field": field,
                "label": _REPAIR_FIELD_LABELS[field],
                "value": display,
            }
        )
    return completed


def repair_slot_prompt(state: GraphState) -> dict[str, Any] | None:
    """Build the next single-question prompt for a repair creation flow."""

    if state.intent != "REPAIR" or not state.requested_slot:
        return None
    field = state.requested_slot
    completed = _repair_completed(state)
    common: dict[str, Any] = {
        "field": field,
        "step": len(completed) + 1,
        "total_steps": 2,
        "completed": completed,
    }
    if field == "description":
        return {
            **common,
            "label": "故障现象",
            "prompt": "请描述一下具体出现了什么故障？",
            "help_text": (
                "例如：水管一直漏水、插座频繁跳闸、电梯门无法关闭。系统会根据描述自动归类。"
            ),
            "allow_custom": True,
            "options": [],
        }
    if field == "location":
        return {
            **common,
            "label": "发生地点",
            "prompt": "这个故障发生在哪里？",
            "help_text": "可选择常用位置，也可以输入楼栋、单元或更具体的位置。",
            "allow_custom": True,
            "options": [
                _option(value, value)
                for value in (
                    "客厅",
                    "卧室",
                    "厨房",
                    "卫生间",
                    "阳台",
                    "玄关",
                    "楼道或公共区域",
                    "地下车库",
                )
            ],
        }
    return None
