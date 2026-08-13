"""槽位补全节点 — PRD §6.5.5（必须使用确定性逻辑）。

只追问缺失的必填槽位（PRD §6.5.10），不臆造业务数据。
若上游已选定工具（``slots["tool"]``），按**工具级**必填槽位校验（查询类不会
被写操作的参数卡住）；否则退回意图级必填槽位。
UNCERTAIN / GENERAL_HELP 不需要业务槽位，直接放行。
"""

from property_agent.agent.policies import (
    Intent,
    missing_slots_for,
    missing_slots_for_tool,
)
from property_agent.agent.slot_prompts import repair_slot_prompt

_CLARIFY: dict[str, str] = {
    "REPAIR": "请提供：报修类别、具体位置、问题描述。",
    "ANNOUNCEMENT": "请提供：公告标题、正文、受众范围。",
    "BILLING": "请说明您想查询的账单类型（如物业费、欠费）。",
    "INSPECTION": "请说明需要的巡检操作（如查询任务、提交记录、上报事件）。",
}

_FIELD_LABELS = {
    "task_id": "巡检任务",
    "event_id": "安防事件",
    "title": "任务标题",
    "description": "情况说明",
    "point": "巡检点位",
    "note": "记录内容",
    "event_type": "事件类型",
    "risk_level": "风险等级",
    "location": "发生位置",
    "record_type": "记录类型",
    "topic": "公告主题",
    "audience": "公告受众",
    "scheduled_at": "发布时间",
    "revision_instruction": "需要修改的具体内容",
}


def collect_slots_node():
    def node(state):
        if state.intent in (Intent.UNCERTAIN.value, Intent.GENERAL_HELP.value):
            return state
        tool = state.slots.get("tool")
        if tool:
            missing = missing_slots_for_tool(tool, state.slots)
        else:
            missing = missing_slots_for(state.intent, state.slots)
        state.missing_slots = missing
        state.requested_slot = missing[0] if missing else None
        if missing:
            repair_prompt = repair_slot_prompt(state)
            if repair_prompt is not None:
                state.add_message(
                    "assistant",
                    repair_prompt["prompt"],
                    kind="slot_prompt",
                    field=repair_prompt["field"],
                )
            elif state.intent in {Intent.INSPECTION.value, Intent.ANNOUNCEMENT.value}:
                if (
                    state.intent == Intent.ANNOUNCEMENT.value
                    and missing[0] == "revision_instruction"
                    and state.slots.get("revision_detail_kind") == "event_time"
                ):
                    state.add_message(
                        "assistant",
                        "请告诉我事项的开始时间和预计结束时间，例如“明天上午9点至下午4点”。公告发布时间会继续按原安排保留。",
                    )
                else:
                    labels = "、".join(_FIELD_LABELS.get(item, item) for item in missing)
                    state.add_message("assistant", f"请补充{labels}。")
            else:
                prompt = _CLARIFY.get(state.intent, "请补充必要信息。")
                state.add_message("assistant", prompt)
        return state

    return node
