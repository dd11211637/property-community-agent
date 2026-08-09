"""结果解释节点 — PRD §6.5.2（事实与建议分离）。

只根据工具返回的**事实**作答：
* 工具抛错 —— 如实展示错误；
* 工具返回接管指令 —— 说明已转人工，并同步 ``handover_required``；
* 工具返回业务失败（如账单源不可用）—— 展示真实错误码，不编造数据；
* 成功 —— 给出可核对的要点摘要。
"""

from typing import Any

from property_agent.agent.state import GraphState


def _facts(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    if "count" in data:
        return f"共 {data['count']} 条"
    for key in ("work_order", "task", "announcement", "consultation", "bill"):
        obj = data.get(key)
        if isinstance(obj, dict):
            ident = obj.get("business_no") or obj.get("id") or obj.get("bill_id")
            status = obj.get("status")
            return f"{key}={ident}" + (f"，状态={status}" if status else "")
    return "成功"


def explain_result_node():
    def node(state: GraphState) -> GraphState:
        if state.error:
            state.add_message("assistant", f"操作未能完成：{state.error}")
            return state

        result = state.tool_result or {}
        if result.get("handover_required") or state.handover_required:
            state.handover_required = True
            reason = result.get("reason") or "该操作为高风险，需授权人工处理。"
            state.add_message("assistant", f"已转人工处理：{reason}")
            return state

        if result.get("ok") is False:
            reason = result.get("reason") or result.get("error_code") or "未知错误"
            state.add_message("assistant", f"操作未能完成：{reason}")
            return state

        summary = result.get("summary") or _facts(result)
        state.add_message("assistant", f"已完成：{state.intent}。结果：{summary}")
        return state

    return node
