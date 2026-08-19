"""评估路由：能精确比较的走规则，只能语义判断的走 LLM Judge。

决策优先级与用户约定一致：
1. evaluator 显式指定 rule / llm 时直接服从；
2. auto + 有规则规格 → 规则（确定性优先）；
3. auto + 仅有 LLM 规格 → LLM Judge；
4. 两者都没有 → 配置错误，抛出。
"""

from __future__ import annotations

from judge.schemas import Check


class RoutingError(ValueError):
    """check 既没有规则规格也没有 LLM 规格。"""


def route(check: Check) -> str:
    """返回 'rule' 或 'llm'。"""
    if check.evaluator == "rule":
        if check.rule is None:
            raise RoutingError(f"check {check.id} 指定 rule 但缺少规则规格")
        return "rule"
    if check.evaluator == "llm":
        if check.llm is None:
            raise RoutingError(f"check {check.id} 指定 llm 但缺少评审规格")
        return "llm"
    if check.rule is not None:
        return "rule"
    if check.llm is not None:
        return "llm"
    raise RoutingError(f"check {check.id} 无任何可执行的评估规格")
