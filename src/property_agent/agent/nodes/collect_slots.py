"""槽位补全节点 — PRD §6.5.5（必须使用确定性逻辑）。

只追问缺失的必填槽位（PRD §6.5.10），不臆造业务数据。UNCERTAIN / GENERAL_HELP
不需要业务槽位，直接放行。
"""

from property_agent.agent.policies import Intent, missing_slots_for

_CLARIFY: dict[str, str] = {
    "REPAIR": "请提供：报修类别、具体位置、问题描述。",
    "ANNOUNCEMENT": "请提供：公告标题、正文、受众范围。",
    "BILLING": "请说明您想查询的账单类型（如物业费、欠费）。",
    "INSPECTION": "请说明需要的巡检操作（如查询任务、提交记录、上报事件）。",
}


def collect_slots_node():
    def node(state):
        if state.intent in (Intent.UNCERTAIN.value, Intent.GENERAL_HELP.value):
            return state
        missing = missing_slots_for(state.intent, state.slots)
        state.missing_slots = missing
        if missing:
            prompt = _CLARIFY.get(state.intent, "请补充必要信息。")
            state.add_message("assistant", f"{prompt}（缺失：{', '.join(missing)}）")
        return state

    return node
