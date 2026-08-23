"""执行工具节点 — PRD §6.5.2（AI 层只调用公开 Application Service 工具）。

工具通过可插拔注册表注入（与具体模块解耦）；工具失败展示真实错误状态，
不把模型回答当作业务成功证据（PRD §6.5.2）。
"""

from collections.abc import Mapping

from property_agent.agent.state import GraphState
from property_agent.agent.working_state import synchronize_typed_domain


def execute_tool_node(registry: Mapping):
    def node(state: GraphState) -> GraphState:
        action = state.pending_action or {}
        tool_name = action.get("tool")
        tool = registry.get(tool_name) if tool_name else None
        if tool is None:
            state.error = f"unknown or missing tool: {tool_name}"
            return state
        try:
            state.tool_result = tool(state)
        except Exception as exc:  # 工具失败展示真实错误（PRD §6.5.10）
            state.error = str(exc)
            state.retry_count += 1
        synchronize_typed_domain(state)
        return state

    return node
