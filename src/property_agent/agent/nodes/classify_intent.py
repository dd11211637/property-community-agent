"""意图识别节点 — PRD §6.5.5（允许使用 LLM 的节点）。

通过可插拔 ``ModelGateway`` 分类意图；低置信度降为 UNCERTAIN 交由澄清；
模型不可用时降级为 UNCERTAIN 并提示用户直接说明业务（PRD R-02）。
"""

from property_agent.agent.model_gateway import ModelGateway
from property_agent.agent.policies import Intent

LOW_CONFIDENCE = 0.5


def classify_intent_node(gateway: ModelGateway):
    def node(state):
        if state.intent and state.intent != Intent.UNCERTAIN.value:
            return state  # 已由结构化输入或上游设定
        if not gateway.ready():
            state.intent = Intent.UNCERTAIN.value
            state.confidence = 0.0
            state.add_message(
                "assistant",
                "智能识别暂不可用，请说明要办理的业务：报修 / 公告 / 账单 / 巡检。",
            )
            return state
        intent, conf = gateway.classify_intent(state.slots.get("user_text", ""))
        state.intent = intent
        state.confidence = conf
        if conf < LOW_CONFIDENCE:
            state.intent = Intent.UNCERTAIN.value
            state.add_message(
                "assistant",
                "不太确定您的需求，请选择：报修 / 公告 / 账单 / 巡检 / 帮助。",
            )
        return state

    return node
