"""报告生成：批次聚合 + JSON / Markdown 输出。

输出约定：
- 最新报告固定写在 out/ 根目录（results.json + summary.md）；
- 每次生成同时归档到 out/history/<生成时间>/，便于对比多次评测；
- 时间统一使用北京时间（UTC+8）中文格式标注。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from judge.schemas import CaseResult, FailureCategory, Metric, RunReport

REPORT_SUMMARY = "summary.md"
REPORT_DETAIL = "results.json"
HISTORY_DIR = "history"
BEIJING_TZ = timezone(timedelta(hours=8))

METRIC_LABELS: dict[str, str] = {
    Metric.TASK_SUCCESS.value: "任务成功",
    Metric.TOOL_CORRECTNESS.value: "工具正确性",
    Metric.WORKFLOW_CORRECTNESS.value: "流程正确性",
    Metric.ANSWER_QUALITY.value: "回答质量",
    Metric.INSTRUCTION_FOLLOWING.value: "指令遵循",
    Metric.SAFETY.value: "安全性",
}

FAILURE_LABELS: dict[str, str] = {
    FailureCategory.INTENT_MISROUTE.value: "意图误判",
    FailureCategory.SLOT_MISSING.value: "槽位缺失",
    FailureCategory.TOOL_MISSING.value: "工具缺失",
    FailureCategory.TOOL_WRONG.value: "工具误用",
    FailureCategory.TOOL_ORDER.value: "工具顺序错误",
    FailureCategory.PARAM_WRONG.value: "参数错误",
    FailureCategory.CONFIRMATION_BYPASS.value: "绕过确认",
    FailureCategory.HANDOVER_MISSING.value: "缺少转人工",
    FailureCategory.ANSWER_INCOMPLETE.value: "回答不完整",
    FailureCategory.ANSWER_WRONG.value: "回答错误",
    FailureCategory.ANSWER_HALLUCINATED.value: "回答虚构",
    FailureCategory.INSTRUCTION_VIOLATION.value: "违反指令",
    FailureCategory.SAFETY_VIOLATION.value: "安全违规",
    FailureCategory.DEGRADATION.value: "降级",
    FailureCategory.UNEVALUATED.value: "未评估",
}


EVALUATOR_LABELS = {"rule": "规则", "llm": "语义", "pending_human": "待人工"}


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def format_beijing(value: datetime) -> str:
    local = value.astimezone(BEIJING_TZ)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def build_report(
    results: list[CaseResult], high_risk_cases: list[str] | None = None
) -> RunReport:
    failed = [r.case_id for r in results if r.overall < 1.0 and not r.needs_human_review]
    review = [r.case_id for r in results if r.needs_human_review]
    average = round(sum(r.overall for r in results) / len(results), 4) if results else 0.0
    return RunReport(
        generated_at=beijing_now(),
        total_cases=len(results),
        results=results,
        average_overall=average,
        failed_cases=failed,
        human_review_cases=review,
        high_risk_cases=list(high_risk_cases or []),
    )


def write_report(report: RunReport, out_dir: str | Path) -> tuple[Path, Path]:
    """写出最新报告（out/ 根目录）并归档一份到 out/history/<生成时间>/。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(json.loads(report.model_dump_json()), ensure_ascii=False, indent=2)
    markdown = _markdown(report)

    detail_path = out / REPORT_DETAIL
    summary_path = out / REPORT_SUMMARY
    detail_path.write_text(payload, encoding="utf-8")
    summary_path.write_text(markdown, encoding="utf-8")

    stamp = report.generated_at.astimezone(BEIJING_TZ).strftime("%Y%m%d-%H%M%S")
    archive_dir = out / HISTORY_DIR / stamp
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / REPORT_DETAIL).write_text(payload, encoding="utf-8")
    (archive_dir / REPORT_SUMMARY).write_text(markdown, encoding="utf-8")
    return detail_path, summary_path


def _failure_label(value: str) -> str:
    return f"{FAILURE_LABELS.get(value, value)}({value})"


def _markdown(report: RunReport) -> str:
    lines = [
        "# Agent 评测报告",
        "",
        f"- **生成时间**：{format_beijing(report.generated_at)}（北京时间）",
        f"- 用例总数：{report.total_cases}",
        f"- 平均 Overall：{report.average_overall}",
        f"- 未达标用例：{'、'.join(report.failed_cases) or '无'}",
        f"- 待人工抽检：{'、'.join(report.human_review_cases) or '无'}",
        "",
        "## 用例总览",
        "",
        "| 用例 | Overall | 安全门禁 | 失败归因 | 待人工抽检 |",
        "|---|---|---|---|---|",
    ]
    for result in report.results:
        gate = "❌ 不通过" if result.safety_gate_failed else "✅ 通过"
        review = "是" if result.needs_human_review else "否"
        categories = "、".join(_failure_label(c) for c in result.failure_categories) or "—"
        lines.append(f"| {result.case_id} | {result.overall:.2f} | {gate} | {categories} | {review} |")

    lines.extend(["", "## 指标明细", "", "| 用例 | 指标 | 得分 |", "|---|---|---|"])
    for result in report.results:
        if not result.metric_scores:
            lines.append(f"| {result.case_id} | — | — |")
            continue
        for metric, score in result.metric_scores.items():
            label = METRIC_LABELS.get(metric, metric)
            lines.append(f"| {result.case_id} | {label}({metric}) | {score:.2f} |")

    lines.extend(["", "## 失败检查项", ""])
    failed_rows = [
        (
            f"- {result.case_id}/{check.check_id}〔{EVALUATOR_LABELS.get(check.evaluator, check.evaluator)}〕"
            f"{check.evidence} → 归因：{_failure_label(check.failure_category.value)}"
        )
        for result in report.results
        for check in result.checks
        if check.passed is False
    ]
    lines.extend(failed_rows or ["- 无"])

    lines.extend(["", "## 待人工抽检项", ""])
    pending_rows = [
        f"- {result.case_id}/{check.check_id}〔{EVALUATOR_LABELS.get(check.evaluator, check.evaluator)}〕"
        f"（{METRIC_LABELS.get(check.metric.value, check.metric.value)}）"
        for result in report.results
        for check in result.checks
        if check.evaluator == "pending_human" or check.passed is None
    ]
    high_risk_rows = [
        f"- {case_id}：高风险用例，无论得分强制人工复核"
        for case_id in report.high_risk_cases
    ]
    lines.extend(pending_rows or [])
    lines.extend(high_risk_rows or [])
    if not pending_rows and not high_risk_rows:
        lines.append("- 无")

    lines.extend(
        [
            "",
            "---",
            f"*本报告由 llm-as-judge 自动生成于 {format_beijing(report.generated_at)}（北京时间），"
            "归档副本见 history/ 目录。*",
        ]
    )
    return "\n".join(lines) + "\n"
