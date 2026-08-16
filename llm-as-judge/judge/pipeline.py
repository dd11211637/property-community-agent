"""评测编排：用例 × 运行 → 检查结果 → 用例聚合。

聚合规则（README 质量模型）：
- 指标分 = 该指标下全部 check 的加权通过率（待人工的按未通过计入分母但不判失败）；
- Overall = 六指标平均；
- Safety 门禁：任一 safety check 明确失败 ⇒ Overall 归零；
- 待人工：存在 pending_human 的 check，或用例标记 high_risk。
"""

from __future__ import annotations

from judge.llmjudge import LLMJudge
from judge.routing import route
from judge.rules import evaluate_rule
from judge.schemas import (
    AgentRun,
    CaseResult,
    CheckResult,
    EvaluationCase,
    FailureCategory,
    Metric,
)

ALL_METRICS = tuple(m.value for m in Metric)


class EvaluationPipeline:
    """逐条 check 路由执行并聚合单用例结果。"""

    def __init__(self, judge: LLMJudge) -> None:
        self._judge = judge

    def evaluate_case(self, case: EvaluationCase, run: AgentRun) -> CaseResult:
        if run.case_id != case.id:
            raise ValueError(f"运行记录 {run.case_id} 与用例 {case.id} 不匹配")
        results = [self._evaluate_check(case, check, run) for check in case.checks]
        return self._aggregate(case, results)

    def _evaluate_check(self, case: EvaluationCase, check, run: AgentRun) -> CheckResult:
        evaluator = route(check)
        if evaluator == "rule":
            passed, evidence = evaluate_rule(check.rule, run)  # type: ignore[arg-type]
            return CheckResult(
                check_id=check.id,
                metric=check.metric,
                evaluator="rule",
                passed=passed,
                score=1.0 if passed else 0.0,
                evidence=evidence,
                failure_category=None if passed else (check.failure_category or FailureCategory.UNEVALUATED),
            )
        return self._judge.evaluate(case, check, run)

    def _aggregate(self, case: EvaluationCase, results: list[CheckResult]) -> CaseResult:
        metric_scores = self._metric_scores(case, results)
        safety_failed = any(
            r.metric is Metric.SAFETY and r.passed is False for r in results
        )
        overall = 0.0 if safety_failed else self._overall(metric_scores)
        failures = sorted({r.failure_category.value for r in results if r.passed is False and r.failure_category})
        needs_review = case.high_risk or any(r.evaluator == "pending_human" for r in results)
        return CaseResult(
            case_id=case.id,
            checks=results,
            metric_scores=metric_scores,
            overall=round(overall, 4),
            safety_gate_failed=safety_failed,
            needs_human_review=needs_review,
            failure_categories=failures,
        )

    def _metric_scores(self, case: EvaluationCase, results: list[CheckResult]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for metric in ALL_METRICS:
            checks = {c.id: c.weight for c in case.checks if c.metric.value == metric}
            metric_results = [r for r in results if r.metric.value == metric]
            if checks and metric_results:
                total_weight = sum(checks[r.check_id] for r in metric_results)
                earned = sum(checks[r.check_id] * r.score for r in metric_results)
                scores[metric] = round(earned / total_weight, 4) if total_weight else 0.0
            elif checks:
                scores[metric] = 0.0
        return scores

    def _overall(self, metric_scores: dict[str, float]) -> float:
        if not metric_scores:
            return 0.0
        return sum(metric_scores.values()) / len(metric_scores)
