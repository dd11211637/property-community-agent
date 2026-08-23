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
from property_agent.agent.capabilities.compatibility import migrated_presentation
from property_agent.agent.slot_prompts import repair_slot_prompt
from property_agent.agent.state import GraphState


def _repair_slot_prompt(state) -> dict[str, Any] | None:
    return repair_slot_prompt(state)


def _pending_card(pending: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pending:
        return None
    summaries = {
        name: str(metadata["confirmation_title"])
        for name, metadata in migrated_presentation().items()
        if metadata["confirmation_title"]
    }
    tool = pending.get("tool")
    params = pending.get("params", {})
    if tool == "security_event_create" and params.get("risk_level") == "HIGH_RISK":
        summary = (
            "高风险提示：请优先远离危险区域，不要触碰可疑设备或明火；"
            "如存在即时人身危险，请立即联系当地紧急救援。确认后系统会"
            "上报事件并通知值班人员。"
        )
    else:
        summary = summaries.get(str(tool), "请确认是否继续提交。")
    return {
        "summary": summary,
        "tool": tool,
        "params": params,
        "action_hash": pending.get("params_hash"),
        "issued_at": pending.get("issued_at"),
    }


def _facts(tool_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not tool_result or tool_result.get("ok") is not True:
        return None
    return tool_result.get("data")


def _generic_slot_prompt(state: GraphState) -> dict[str, Any] | None:
    selection = state.slots.get("_selection_options")
    if isinstance(selection, dict) and selection.get("options"):
        return selection
    if not state.missing_slots:
        return None
    field = state.missing_slots[0]
    inspection_prompts: dict[str, dict[str, Any]] = {
        "topic": {
            "label": "公告主题",
            "prompt": "请说明公告主题和需要包含的关键信息",
            "options": [],
        },
        "audience": {
            "label": "公告受众",
            "prompt": "请选择公告受众，也可以输入具体楼栋",
            "options": [
                {"label": "全社区", "value": {}},
                {"label": "1栋住户", "value": {"building_ids": ["1栋"]}},
                {"label": "2栋住户", "value": {"building_ids": ["2栋"]}},
            ],
        },
        "scheduled_at": {
            "label": "发布时间",
            "prompt": "请输入带时区的发布时间，例如 2026-08-15T09:00:00+08:00",
            "options": [],
        },
        "title": {
            "label": "任务标题",
            "prompt": "请填写巡检任务标题",
            "options": [],
        },
        "description": {
            "label": "情况说明",
            "prompt": "请描述巡检目标或现场发现的问题",
            "options": [],
        },
        "location": {
            "label": "发生位置",
            "prompt": "请选择事件发生位置，也可以输入更具体的位置",
            "options": [
                {"label": "小区出入口", "value": "小区出入口"},
                {"label": "楼栋大厅", "value": "楼栋大厅"},
                {"label": "消防通道", "value": "消防通道"},
                {"label": "地下车库", "value": "地下车库"},
                {"label": "公共设备间", "value": "公共设备间"},
            ],
        },
        "point": {
            "label": "巡检点位",
            "prompt": "请选择巡检点位，也可以输入更具体的位置",
            "options": [
                {"label": "小区出入口", "value": "小区出入口"},
                {"label": "楼栋大厅", "value": "楼栋大厅"},
                {"label": "消防通道", "value": "消防通道"},
                {"label": "地下车库", "value": "地下车库"},
                {"label": "公共设备间", "value": "公共设备间"},
            ],
        },
        "note": {
            "label": "记录内容",
            "prompt": "请说明巡检发现或事件处置结果",
            "options": [],
        },
        "event_type": {
            "label": "事件类型",
            "prompt": "请选择最接近的事件类型",
            "options": [
                {"label": "燃气泄漏", "value": "GAS_LEAK"},
                {"label": "火情", "value": "FIRE"},
                {"label": "人员安全", "value": "PERSONAL_SAFETY"},
                {"label": "设施设备隐患", "value": "EQUIPMENT_FAULT"},
                {"label": "其他事件", "value": "OTHER"},
            ],
        },
        "record_type": {
            "label": "记录类型",
            "prompt": "请选择巡检记录类型",
            "options": [
                {"label": "点位记录", "value": "POINT_RECORD"},
                {"label": "过程记录", "value": "PROGRESS"},
                {"label": "完成记录", "value": "COMPLETION"},
            ],
        },
    }
    prompt = inspection_prompts.get(field)
    return {"field": field, "allow_custom": True, **prompt} if prompt else None


def turn_data(turn: AgentTurn) -> dict[str, Any]:
    state = turn.state
    interrupt = turn.interrupt if isinstance(turn.interrupt, dict) else None
    pending = _pending_card(interrupt.get("action") if interrupt else None)
    # A completed slot flow can still have the previous "missing field"
    # prompt as its latest assistant message.  Once confirmation is ready,
    # that prompt is stale and must not be rendered again.
    reply = "" if pending is not None else turn.reply
    return {
        "conversation_id": state.conversation_id,
        "status": turn.conversation.status,
        "done": turn.done,
        "intent": state.intent,
        "confidence": state.confidence,
        "operation_level": state.operation_level,
        "reply": reply,
        "messages": state.messages,
        "facts": _facts(state.tool_result),
        # Trace is deliberately independent from successful business facts so
        # failed read runs remain diagnosable. It contains tool names, hashes
        # and result summaries only; never raw arguments or model reasoning.
        "agent_trace": state.read_trace,
        "missing_slots": list(state.missing_slots),
        "requested_slot": state.requested_slot,
        "slot_prompt": _repair_slot_prompt(state) or _generic_slot_prompt(state),
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
