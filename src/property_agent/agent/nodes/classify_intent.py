"""意图识别节点 — PRD §6.5.5（允许使用 LLM 的节点）。

通过可插拔 ``ModelGateway`` 分类意图；低置信度降为 UNCERTAIN 交由澄清；
模型不可用时降级为 UNCERTAIN 并提示用户直接说明业务（PRD R-02）。
"""

from property_agent.agent.model_gateway import ModelGateway
from property_agent.agent.policies import Intent

LOW_CONFIDENCE = 0.5

# Values in these fields must only come from authenticated request context or
# deterministic platform services, never from model output.
_TRUSTED_OR_CONTROL_SLOTS = frozenset(
    {
        "actor_id",
        "community_id",
        "current_house_id",
        "house_id",
        "bound_house_ids",
        "roles",
        "request_id",
        "confirmation_token",
        "idempotency_key",
        "expected_version",
        "tool",
        "user_text",
    }
)


def _merge_model_slots(state, suggested: dict) -> None:
    corrected_fields = set(state.slots.get("_user_corrected_fields") or ())
    for key, value in suggested.items():
        if key in _TRUSTED_OR_CONTROL_SLOTS or value is None:
            continue
        if key in corrected_fields:
            continue
        # An empty audience condition is a complete, meaningful value: it
        # means the whole community. Do not let a later model turn replace the
        # validated object with a display string such as "全社区".
        if (
            state.intent == Intent.ANNOUNCEMENT.value
            and key in {"title", "body", "category", "audience"}
            and key in state.slots
            and str(state.slots.get("action") or "") != "revise"
        ):
            # These fields are the validated output of announcement_draft.
            # A later conversational instruction ("use this draft") may select
            # an action but must never rewrite reviewed content or its category.
            continue
        if state._contextual_followup or key not in state.slots or not state.slots[key]:
            state.slots[key] = value


def classify_intent_node(gateway: ModelGateway):
    def node(state):
        previous_intent = state.intent if state._continuation else None
        if state.intent and state.intent != Intent.UNCERTAIN.value and not state._continuation:
            return state  # 已由结构化输入或上游设定
        if not gateway.ready():
            state.intent = Intent.UNCERTAIN.value
            state.confidence = 0.0
            state.add_message(
                "assistant",
                "智能识别暂不可用，请说明要办理的业务：报修 / 公告 / 账单 / 巡检。",
            )
            return state
        analyze_with_context = getattr(gateway, "analyze_with_context", None)
        result = (
            analyze_with_context(
                state.slots.get("user_text", ""),
                history=state.messages[:-1],
                trusted_context=state.trusted_context,
            )
            if analyze_with_context is not None
            else gateway.analyze(state.slots.get("user_text", ""))
        )
        _merge_model_slots(state, result.slots)
        if previous_intent and (
            result.intent == Intent.UNCERTAIN.value or result.confidence < LOW_CONFIDENCE
        ):
            # A short follow-up such as "厨房" may contain only the missing value.
            # Keep the already established domain while accepting explicit slot suggestions.
            state.intent = previous_intent
            state.confidence = max(state.confidence, result.confidence)
        else:
            state.intent = result.intent
            state.confidence = result.confidence
        state._continuation = False
        state._contextual_followup = False
        if result.degraded:
            state.add_message(
                "assistant",
                "智能模型暂不可用，已切换到关键词识别；您仍可继续办理结构化业务。",
            )
        if not previous_intent and result.confidence < LOW_CONFIDENCE:
            state.intent = Intent.UNCERTAIN.value
            state.add_message(
                "assistant",
                "不太确定您的需求，请选择：报修 / 公告 / 账单 / 巡检 / 帮助。",
            )
        return state

    return node
