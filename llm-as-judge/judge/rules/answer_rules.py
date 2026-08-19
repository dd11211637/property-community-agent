"""最终回复类确定性规则：精确匹配 / 包含 / 正则。"""

from __future__ import annotations

import re

from judge.schemas import AgentRun, RuleSpec


def evaluate_answer_rule(spec: RuleSpec, run: AgentRun) -> tuple[bool, str]:
    """返回 (是否通过, 证据描述)。kind 不认识时抛 ValueError。"""
    answer = run.final_answer
    kind, params = spec.kind, spec.params
    if kind == "exact_match":
        expected = str(params["expected"])
        return answer.strip() == expected.strip(), f"answer={'匹配' if answer.strip() == expected.strip() else '不匹配'} {expected!r}"
    if kind == "contains_all":
        terms = [str(t) for t in params["terms"]]
        missing = [t for t in terms if t not in answer]
        return not missing, f"缺失关键词: {missing}" if missing else f"全部命中: {terms}"
    if kind == "contains_any":
        terms = [str(t) for t in params["terms"]]
        hits = [t for t in terms if t in answer]
        return bool(hits), f"命中: {hits}" if hits else f"均未命中: {terms}"
    if kind == "not_contains":
        terms = [str(t) for t in params["terms"]]
        leaked = [t for t in terms if t in answer]
        return not leaked, f"不应出现的内容出现: {leaked}" if leaked else "未出现禁用内容"
    if kind == "regex":
        pattern = str(params["pattern"])
        found = re.search(pattern, answer) is not None
        return found, f"regex {pattern!r} {'命中' if found else '未命中'}"
    if kind == "not_regex":
        pattern = str(params["pattern"])
        match = re.search(pattern, answer)
        return match is None, (
            f"命中禁用模式 {pattern!r}: {match.group(0)!r}" if match else f"未命中禁用模式 {pattern!r}"
        )
    raise ValueError(f"非最终回复规则: {kind}")
