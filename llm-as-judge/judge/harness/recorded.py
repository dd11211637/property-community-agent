"""录制回放 harness：用已保存的运行记录做离线评估。"""

from __future__ import annotations

from pathlib import Path

from judge.harness.base import AgentHarnessPort
from judge.schemas import AgentRun, CaseInput


class RecordedHarness:
    """从 runs/ 目录按 case_id 查找已录制运行；缺失即抛错，禁止编造轨迹。"""

    def __init__(self, runs_dir: str | Path) -> None:
        self._root = Path(runs_dir)

    def run(self, case_id: str, case_input: CaseInput) -> AgentRun:
        path = self._root / f"{case_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"缺少运行记录: {path}（先录制或提供联机 harness）")
        return AgentRun.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = ["AgentHarnessPort", "RecordedHarness", "record_run"]
