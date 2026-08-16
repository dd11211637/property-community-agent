"""命令行入口。

用法（在 llm-as-judge/ 目录下）:
    python -m judge run --cases cases --runs runs --out reports   # 回放已录制运行
    python -m judge run --live --cases cases --runs runs --out reports  # 现场驱动真实后端
    python -m judge list --cases cases
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from judge.harness import RecordedHarness
from judge.loader import load_cases
from judge.llmjudge import DeepSeekClient, LLMJudge
from judge.pipeline import EvaluationPipeline
from judge.report import build_report, write_report
from judge.routing import RoutingError


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            return _list_cases(args.cases)
        return _run_evaluation(args)
    except (FileNotFoundError, ValueError, RoutingError) as exc:
        print(f"[judge] 配置或数据错误: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"[judge] 联机运行失败: {exc}", file=sys.stderr)
        return 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="judge", description="Agent 双路评测系统")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="执行评测批次")
    run.add_argument("--cases", default="cases", help="用例目录")
    run.add_argument("--runs", default="runs", help="运行记录目录")
    run.add_argument("--out", default="reports", help="报告输出目录")
    run.add_argument("--no-llm", action="store_true", help="跳过 LLM Judge（语义项转待人工）")
    run.add_argument(
        "--live",
        action="store_true",
        help="联机模式：驱动真实后端现场运行 Agent，并把转录保存到 --runs 目录",
    )
    run.add_argument("--base-url", default="http://127.0.0.1:8000", help="联机模式的后端地址")

    listing = sub.add_parser("list", help="列出用例")
    listing.add_argument("--cases", default="cases", help="用例目录")
    return parser


def _run_evaluation(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    harness = _build_harness(args)
    judge = None if args.no_llm else LLMJudge(DeepSeekClient())
    pipeline = EvaluationPipeline(judge or LLMJudge(None))
    results = []
    missing: list[str] = []
    for case in cases:
        try:
            run = harness.run(case.id, case.input)
        except FileNotFoundError:
            missing.append(case.id)
            continue
        results.append(pipeline.evaluate_case(case, run))
    if missing:
        print(f"[judge] 缺少运行记录，已跳过: {missing}", file=sys.stderr)
    if not results:
        print("[judge] 没有任何可评估的运行记录", file=sys.stderr)
        return 2
    report = build_report(results, high_risk_cases=[c.id for c in cases if c.high_risk])
    detail, summary = write_report(report, args.out)
    _print_summary(report)
    print(f"[judge] 明细: {detail}")
    print(f"[judge] 摘要: {summary}")
    return 0


def _build_harness(args: argparse.Namespace):
    """回放模式读取预录数据；联机模式现场驱动真实后端并落盘转录。"""
    if not getattr(args, "live", False):
        return RecordedHarness(args.runs)
    from judge.harness.live import LiveHarness

    live = LiveHarness(args.base_url)
    recorder = _LiveRecorder(live, args.runs)
    recorder.preflight()
    return recorder


class _LiveRecorder:
    """联机 harness 包装：现场运行真实 Agent，同时把转录保存到 runs/ 目录。"""

    def __init__(self, live, runs_dir: str) -> None:
        self._live = live
        self._runs = Path(runs_dir)

    def preflight(self) -> None:
        probe = self._live._client.get("/ready")
        if probe.status_code != 200:
            raise RuntimeError(f"后端未就绪（/ready 返回 {probe.status_code}），请先启动 compose 栈")

    def run(self, case_id: str, case_input):
        run = self._live.run(case_id, case_input)
        self._runs.mkdir(parents=True, exist_ok=True)
        path = self._runs / f"{case_id}.json"
        path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        return run


def _print_summary(report) -> None:
    from judge.report import format_beijing

    print(f"生成时间 {format_beijing(report.generated_at)}（北京时间）")
    print(f"用例数 {report.total_cases}｜平均 Overall {report.average_overall}")
    for result in report.results:
        status = "待人工" if result.needs_human_review else ("不通过" if result.overall < 1.0 else "通过")
        gate = "｜安全门禁不通过" if result.safety_gate_failed else ""
        print(f"  {result.case_id}: {result.overall:.2f} [{status}]{gate}")


def _list_cases(cases_dir: str) -> int:
    for case in load_cases(cases_dir):
        rule_count = sum(1 for c in case.checks if c.rule is not None)
        llm_count = sum(1 for c in case.checks if c.llm is not None)
        flag = "（高风险）" if case.high_risk else ""
        print(f"{case.id} [{case.module}]{flag} {case.name}｜规则 {rule_count} 项 / 语义 {llm_count} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
