"""人工接管节点 — PRD §6.5.7（写-高风险 Agent 不执行，只转授权人工）。

标记 ``handover_required`` 并提示用户已转交；具体接管单的创建由工具层在
写-高风险路径下（或在子图装配时）调用业务 Handover 能力完成。
"""

from property_agent.agent.state import GraphState


def handover_node():
    def node(state: GraphState) -> GraphState:
        state.handover_required = True
        state.add_message("assistant", "该操作为高风险，已转交授权人工处理，请等待结果。")
        return state

    return node
