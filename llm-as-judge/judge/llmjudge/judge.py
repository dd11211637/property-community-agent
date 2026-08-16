"""LLM Judge：按 rubric 语义评审，输出分数 / 证据 / 失败归因。"""

from __future__ import annotations

from typing import Any

from judge.llmjudge.client import DeepSeekClient, JudgeUnavailable
from judge.schemas import AgentRun, Check, CheckResult, EvaluationCase, FailureCategory

PASS_SCORE = 4  # 5 分制，≥4 视为通过

SYSTEM_PROMPT = """你是物业社区 Agent 的质量评审员。你只依据给定的参考事实、运行轨迹和最终回复评分，
不引入外部知识，不脑补未发生的行为。

输出必须是单个 JSON 对象，字段：
- "score": 1~5 整数（1=完全失败，2=重大缺陷，3=部分达成，4=达标，5=优秀）
- "evidence": 字符串数组，引用轨迹步骤或回复原文作为证据
- "failure_category": 从给定列表中选一个；完全合格时填 "unevaluated"
- "reasoning": 一句话说明扣分原因

禁止输出 JSON 以外的任何内容。"""

CATEGORY_HINT = ", ".join(c.value for c in FailureCategory)


class LLMJudge:
    """对单条 check 执行语义评审；模型不可用时返回待人工结果。"""

    def __init__(self, client: DeepSeekClient | Any | None = None) -> None:
        self._client = client

    def evaluate(self, case: EvaluationCase, check: Check, run: AgentRun) -> CheckResult:
        if check.llm is None:
            raise ValueError(f"check {check.id} 未配置 LLM 评审规格")
        if self._client is None:
            return self._pending(check, "未配置 LLM 客户端")
        try:
            verdict = self._client.complete_json(SYSTEM_PROMPT, self._build_user_prompt(case, check, run))
        except JudgeUnavailable as exc:
            return self._pending(check, str(exc))
        return self._to_result(check, verdict)

    def _build_user_prompt(self, case: EvaluationCase, check: Check, run: AgentRun) -> str:
        criteria = [f"- {c}" for c in check.llm.criteria] or ["- 无"]
        constraints = [f"- {c}" for c in case.constraints] or ["- 无"]
        trace = [
            f"- {e.step}. [{e.type}] {e.name} {'ok' if e.ok else 'fail' if e.ok is False else ''} {e.detail}".rstrip()
            for e in run.events
        ] or ["- （空）"]
        lines = [
            f"## 评审目标\n{check.llm.rubric}",
            "## 分项考察点",
            *criteria,
            f"## 参考事实（Ground Truth）\n{case.ground_truth or '（无）'}",
            f"## 期望行为\n{case.expected_behavior or '（无）'}",
            "## 约束",
            *constraints,
            "## 运行轨迹",
            *trace,
            f"## 最终回复\n{run.final_answer or '（空）'}",
            f"## 失败归因候选\n{CATEGORY_HINT}",
            f"本条 check 挂钩指标：{check.metric.value}",
        ]
        return "\n".join(lines)

    def _to_result(self, check: Check, verdict: dict[str, Any]) -> CheckResult:
        try:
            score = int(verdict["score"])
            if not 1 <= score <= 5:
                raise ValueError(f"分数越界: {score}")
        except (KeyError, TypeError, ValueError) as exc:
            return self._pending(check, f"模型输出无法解析: {exc}")
        category = self._category(verdict.get("failure_category"))
        evidence = "; ".join(str(e) for e in verdict.get("evidence", []))
        reasoning = str(verdict.get("reasoning", ""))
        return CheckResult(
            check_id=check.id,
            metric=check.metric,
            evaluator="llm",
            passed=score >= PASS_SCORE,
            score=score / 5,
            evidence=f"{evidence}｜{reasoning}".strip("｜"),
            failure_category=category if score < PASS_SCORE else None,
        )

    def _category(self, raw: Any) -> FailureCategory:
        try:
            return FailureCategory(str(raw))
        except ValueError:
            return FailureCategory.UNEVALUATED

    def _pending(self, check: Check, reason: str) -> CheckResult:
        return CheckResult(
            check_id=check.id,
            metric=check.metric,
            evaluator="pending_human",
            passed=None,
            score=0.0,
            evidence=reason,
            failure_category=FailureCategory.UNEVALUATED,
            error=reason,
        )


__all__ = ["LLMJudge", "PASS_SCORE"]
