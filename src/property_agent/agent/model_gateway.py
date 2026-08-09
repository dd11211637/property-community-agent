"""模型网关 — PRD §6.5.9.

业务层不直接依赖 LangChain / LangGraph 类型；模型调用统一通过 ``ModelGateway``
这一可插拔接缝注入。本文件给出：

* ``ModelGateway`` 协议（意图识别、槽位抽取、可用性）；
* ``DeterministicModelGateway``：基于关键词规则的确定性实现，使整套智能体
  在无 LLM Key 的环境下也能跑通与测试；
* ``UnavailableModelGateway``：模型不可用时使用，触发 PRD R-02 的降级
  （结构化业务接口继续可用，Agent 层转人工/澄清）。

后续接入真实模型时，只需提供另一个实现 ``ModelGateway`` 的类（例如封装
LangChain / 厂商 SDK），业务与图逻辑无需改动。
"""

from typing import Protocol, runtime_checkable

from property_agent.agent.policies import Intent


@runtime_checkable
class ModelGateway(Protocol):
    def ready(self) -> bool:
        """模型是否可用（不可用时触发降级）。"""
        ...

    def classify_intent(self, text: str) -> tuple[str, float]:
        """返回 (意图, 置信度)。"""
        ...

    def extract_slots(self, text: str, intent: str) -> dict:
        """从自然语言中抽取非确定性槽位。"""
        ...


class DeterministicModelGateway:
    """关键词规则的确定性意图分类器（演示 / 测试用）。"""

    INTENT_KEYWORDS: dict[str, list[str]] = {
        "REPAIR": ["报修", "维修", "坏了", "漏水", "故障", "修", "破损", "堵塞"],
        "ANNOUNCEMENT": ["公告", "通知", "通告", "发布", "告示"],
        "BILLING": ["账单", "缴费", "物业费", "费用", "收费", "欠费"],
        "INSPECTION": ["巡检", "安保", "巡逻", "安防", "隐患", "治安"],
        "GENERAL_HELP": [
            "帮助",
            "帮忙",
            "你好",
            "您好",
            "能做什么",
            "怎么用",
            "使用说明",
            "服务范围",
            "社区服务",
            "守则",
        ],
    }

    def ready(self) -> bool:
        return True

    def classify_intent(self, text: str) -> tuple[str, float]:
        text = text or ""
        scores: dict[str, int] = {}
        for intent, keywords in self.INTENT_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    scores[intent] = scores.get(intent, 0) + 1
        if not scores:
            return Intent.UNCERTAIN.value, 0.3
        best = max(scores, key=scores.get)
        confidence = min(0.95, 0.6 + 0.1 * scores[best])
        return best, confidence

    def extract_slots(self, text: str, intent: str) -> dict:
        # 确定性槽位由 collect_slots 节点追问补全；这里仅做透传占位。
        return {}


class UnavailableModelGateway:
    """模型不可用：所有模型调用抛出异常，由上层降级处理。"""

    def ready(self) -> bool:
        return False

    def classify_intent(self, text: str) -> tuple[str, float]:
        raise RuntimeError("Model gateway is unavailable.")

    def extract_slots(self, text: str, intent: str) -> dict:
        raise RuntimeError("Model gateway is unavailable.")
