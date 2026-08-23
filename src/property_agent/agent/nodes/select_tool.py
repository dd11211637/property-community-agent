"""工具选择节点 — PRD §6.5.5 / §6.5.7。

工具选择必须是**确定性**的：由已结构化的槽位（``action`` / 关键 ID）决定，
不让模型自由挑选写操作工具。选定结果写入 ``slots["tool"]``，供槽位补全按
工具级必填项校验、并供确认节点判定操作等级。
"""

from collections.abc import Callable

from property_agent.agent.state import GraphState
from property_agent.agent.working_state import synchronize_typed_domain

ToolSelector = Callable[[GraphState], str]


def select_tool_node(selector: ToolSelector):
    def node(state: GraphState) -> GraphState:
        state.slots["tool"] = selector(state)
        synchronize_typed_domain(state)
        return state

    return node
