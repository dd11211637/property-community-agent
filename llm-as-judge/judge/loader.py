"""用例与运行记录加载：cases/ 与 runs/ 目录 → 结构化对象。"""

from __future__ import annotations

import json
from pathlib import Path

from judge.schemas import AgentRun, EvaluationCase


def load_cases(directory: str | Path) -> list[EvaluationCase]:
    """加载目录下全部 *.json 用例；重名 id 视为配置错误直接抛出。"""
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"用例目录不存在: {root}")
    cases: list[EvaluationCase] = []
    seen: dict[str, str] = {}
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        case = EvaluationCase.model_validate(data)
        if case.id in seen:
            raise ValueError(f"用例 id 重复: {case.id}（{seen[case.id]} 与 {path}）")
        seen[case.id] = str(path)
        cases.append(case)
    if not cases:
        raise ValueError(f"用例目录为空: {root}")
    return cases


def load_run(run_path: str | Path) -> AgentRun:
    """加载单个运行记录文件。"""
    data = json.loads(Path(run_path).read_text(encoding="utf-8"))
    return AgentRun.model_validate(data)


def load_runs(directory: str | Path, case_ids: list[str]) -> dict[str, AgentRun]:
    """按用例 id 加载 runs/<case_id>.json；缺失的用例在返回 dict 中没有键。"""
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"运行记录目录不存在: {root}")
    runs: dict[str, AgentRun] = {}
    for case_id in case_ids:
        path = root / f"{case_id}.json"
        if path.is_file():
            runs[case_id] = load_run(path)
    return runs
