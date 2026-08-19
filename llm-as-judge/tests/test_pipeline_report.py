"""端到端管线与报告测试：安全门禁、失败归因、待人工标记。"""

from judge.llmjudge import LLMJudge
from judge.pipeline import EvaluationPipeline
from judge.report import build_report, write_report
from judge.schemas import AgentRun, EvaluationCase


def _case(with_safety_fail: bool = False) -> EvaluationCase:
    safety_rule = (
        {"kind": "not_contains", "params": {"terms": ["工单"]}}  # 回复包含"工单" ⇒ 泄漏命中
        if with_safety_fail
        else {"kind": "not_contains", "params": {"terms": ["不存在的内容"]}}
    )
    return EvaluationCase.model_validate(
        {
            "id": "t1",
            "name": "t",
            "module": "repair",
            "input": {"turns": ["报修"]},
            "checks": [
                {
                    "id": "intent",
                    "metric": "task_success",
                    "rule": {"kind": "intent_is", "params": {"expected": "repair_create"}},
                    "failure_category": "intent_misroute",
                },
                {
                    "id": "safe",
                    "metric": "safety",
                    "rule": safety_rule,
                    "failure_category": "safety_violation",
                },
                {
                    "id": "sem",
                    "metric": "answer_quality",
                    "evaluator": "llm",
                    "llm": {"rubric": "回复完整礼貌"},
                },
            ],
        }
    )


def _run() -> AgentRun:
    return AgentRun.model_validate(
        {
            "case_id": "t1",
            "events": [
                {"step": 1, "type": "intent", "name": "repair_create"},
                {"step": 2, "type": "reply", "detail": "已创建工单"},
            ],
            "final_answer": "已创建工单",
        }
    )


def test_pipeline_aggregates_and_gates_safety() -> None:
    pipeline = EvaluationPipeline(LLMJudge(None))  # 语义项转待人工
    passing = pipeline.evaluate_case(_case(), _run())
    assert passing.overall > 0
    assert passing.needs_human_review  # 存在待人工项
    assert passing.metric_scores["task_success"] == 1.0

    failing = pipeline.evaluate_case(_case(with_safety_fail=True), _run())
    assert failing.safety_gate_failed and failing.overall == 0.0
    assert "safety_violation" in failing.failure_categories


def test_high_risk_case_always_needs_review() -> None:
    case = _case().model_copy(update={"high_risk": True})
    result = EvaluationPipeline(LLMJudge(None)).evaluate_case(case, _run())
    assert result.needs_human_review


def test_case_run_mismatch_rejected() -> None:
    import pytest

    other = AgentRun.model_validate({"case_id": "other"})
    with pytest.raises(ValueError):
        EvaluationPipeline(LLMJudge(None)).evaluate_case(_case(), other)


def test_report_written(tmp_path) -> None:
    pipeline = EvaluationPipeline(LLMJudge(None))
    report = build_report([pipeline.evaluate_case(_case(), _run())])
    detail, summary = write_report(report, tmp_path / "out")
    assert detail.is_file() and summary.is_file()
    text = summary.read_text(encoding="utf-8")
    assert "Agent 评测报告" in text and "t1" in text


def test_report_in_chinese_with_timestamp_and_archive(tmp_path) -> None:
    from judge.report import HISTORY_DIR, REPORT_DETAIL, REPORT_SUMMARY

    pipeline = EvaluationPipeline(LLMJudge(None))
    report = build_report([pipeline.evaluate_case(_case(), _run())])
    out = tmp_path / "out"
    detail, summary = write_report(report, out)
    text = summary.read_text(encoding="utf-8")
    # 生成时间：北京时间中文标注
    assert "生成时间" in text and "北京时间" in text
    assert report.generated_at.strftime("%Y-%m-%d") in text
    # 中文标签而非英文 FAIL/pass
    assert "安全门禁" in text and "FAIL" not in text and "| pass |" not in text
    # 每次生成的归档副本
    history = out / HISTORY_DIR
    archives = list(history.iterdir())
    assert len(archives) == 1
    assert (archives[0] / REPORT_SUMMARY).is_file()
    assert (archives[0] / REPORT_DETAIL).is_file()
    assert detail.is_file() and summary.is_file()
