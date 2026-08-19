"""规则评估器统一入口。"""

from __future__ import annotations

from judge.rules.answer_rules import evaluate_answer_rule
from judge.rules.trace_rules import evaluate_trace_rule
from judge.schemas import AgentRun, RuleSpec

ANSWER_KINDS = {"exact_match", "contains_all", "contains_any", "not_contains", "regex", "not_regex"}


def evaluate_rule(spec: RuleSpec, run: AgentRun) -> tuple[bool, str]:
    """按 kind 分派到回复规则或轨迹规则，返回 (通过, 证据)。"""
    if spec.kind in ANSWER_KINDS:
        return evaluate_answer_rule(spec, run)
    return evaluate_trace_rule(spec, run)


__all__ = ["evaluate_rule"]
