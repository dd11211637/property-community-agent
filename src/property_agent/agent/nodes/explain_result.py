"""结果解释节点 — PRD §6.5.2（事实与建议分离）。

若工具执行出错，如实展示错误；否则给出结果摘要。不编造业务数据。
"""

from property_agent.agent.state import GraphState


def explain_result_node():
    def node(state: GraphState) -> GraphState:
        if state.error:
            state.add_message("assistant", f"操作未能完成：{state.error}")
            return state
        result = state.tool_result or {}
        summary = result.get("summary", "成功")
        state.add_message("assistant", f"已完成：{state.intent}。结果：{summary}")
        return state

    return node
