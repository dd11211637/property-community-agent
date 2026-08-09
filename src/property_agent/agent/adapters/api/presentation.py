"""智能体响应渲染 — 事实与建议分离（PRD §6.5.2）。

* ``facts``  —— 业务 Application Service 返回的真实数据，唯一可信的业务事实；
* ``reply`` / ``messages`` —— 面向用户的自然语言表述，属于建议，不作为
  "操作成功"的证据；
* ``pending_confirmation`` —— 待确认卡片，带 ``action_hash`` 参数指纹，
  确认时必须原样回带。
"""

from typing import Any

from property_agent.agent.application.conversation_service import ConversationSnapshot
from property_agent.agent.application.runner import AgentTurn


def _pending_card(pending: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pending:
        return None
    return {
        "summary": f"确认执行 {pending.get('intent')} 操作：{pending.get('tool')}",
        "tool": pending.get("tool"),
        "params": pending.get("params", {}),
        "action_hash": pending.get("params_hash"),
        "issued_at": pending.get("issued_at"),
    }


def _facts(tool_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not tool_result or tool_result.get("ok") is not True:
        return None
    return tool_result.get("data")


def turn_data(turn: AgentTurn) -> dict[str, Any]:
    state = turn.state
    interrupt = turn.interrupt if isinstance(turn.interrupt, dict) else None
    pending = _pending_card(interrupt.get("action") if interrupt else None)
    return {
        "conversation_id": state.conversation_id,
        "status": turn.conversation.status,
        "done": turn.done,
        "intent": state.intent,
        "confidence": state.confidence,
        "operation_level": state.operation_level,
        "reply": turn.reply,
        "messages": state.messages,
        "facts": _facts(state.tool_result),
        "missing_slots": list(state.missing_slots),
        "handover_required": bool(state.handover_required),
        "pending_confirmation": pending,
        "error": state.error,
    }


def status_data(
    conversation: ConversationSnapshot, pending: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "conversation_id": conversation.conversation_id,
        "status": conversation.status,
        "current_house_id": conversation.current_house_id,
        "last_intent": conversation.last_intent,
        "handover_required": conversation.handover_required,
        "handover_ticket_id": conversation.handover_ticket_id,
        "pending_confirmation": _pending_card(pending),
    }


def sse_events(turn: AgentTurn) -> list[tuple[str, dict[str, Any]]]:
    """把一轮结果拆成 SSE 事件序列。

    本实现不做 token 级流式（模型接缝目前是确定性网关），而是按
    "意图 → 文本 → 确认卡片 / 事实 → 结束" 的顺序回放，前端可以用同一套
    事件处理逻辑对接后续的真实流式模型。
    """
    state = turn.state
    events: list[tuple[str, dict[str, Any]]] = [
        ("intent", {"intent": state.intent, "confidence": state.confidence})
    ]
    for message in state.messages:
        if message.get("role") == "assistant":
            events.append(("message", {"content": message.get("content", "")}))
    data = turn_data(turn)
    if data["pending_confirmation"] is not None:
        events.append(("confirmation", data["pending_confirmation"]))
    if data["facts"] is not None:
        events.append(("facts", {"facts": data["facts"]}))
    if data["handover_required"]:
        events.append(("handover", {"conversation_id": state.conversation_id}))
    events.append(("done", {"done": turn.done, "status": data["status"]}))
    return events
