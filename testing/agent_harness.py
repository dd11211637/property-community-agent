"""Offline harness for controlled-read single-step, trajectory and safety evaluation.

This support module never runs in the production application. It uses deterministic
scenario tools and the production planner/guard/runtime contracts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from property_agent.agent.controlled_read import build_controlled_read_node
from property_agent.agent.nodes.explain_result import explain_result_node
from property_agent.agent.read_planner import GatewayReadPlanner
from property_agent.agent.read_tools import read_tool_specs
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import ok


@dataclass(frozen=True, slots=True)
class HarnessResult:
    case_id: str
    passed: bool
    actual_tools: tuple[str, ...]
    reply: str
    failures: tuple[str, ...]


def load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("harness dataset must be a JSON array")
    return value


def run_case(case: dict[str, Any]) -> HarnessResult:
    calls: list[str] = []
    fixtures = case.get("tool_outputs") or {}

    def tool(name: str):
        def invoke(_state, _arguments):
            calls.append(name)
            data = fixtures.get(name) or {}
            if data.get("ok") is False:
                failure = {
                    "ok": False,
                    "tool": name,
                    "error_code": data.get("error_code", "TOOL_FAILED"),
                }
                if data.get("reason"):
                    failure["reason"] = data["reason"]
                return failure
            return ok(name, **data)

        return invoke

    specs = read_tool_specs()
    node = build_controlled_read_node(
        planner=GatewayReadPlanner(object()),
        specs=specs,
        tools={name: tool(name) for name in specs},
    )
    state = GraphState(
        conversation_id=f"harness-{case['case_id']}",
        actor_id=UUID("00000000-0000-0000-0000-000000000001"),
        community_id=UUID("00000000-0000-0000-0000-000000000002"),
        current_house_id=UUID("00000000-0000-0000-0000-000000000003"),
        intent=case["intent"],
        slots={"user_text": case["input"], **(case.get("slots") or {})},
        trusted_context={
            "business_date": "2026-08-12",
            "community_name": "幸福小区",
            "building": "1",
        },
    )
    result = node(state)
    result = explain_result_node()(result)
    reply = str(result.messages[-1]["content"] if result.messages else "")
    failures: list[str] = []
    required = set(case.get("required_tools") or [])
    forbidden = set(case.get("forbidden_tools") or [])
    if not required.issubset(calls):
        failures.append(f"missing tools: {sorted(required - set(calls))}")
    if forbidden & set(calls):
        failures.append(f"forbidden tools: {sorted(forbidden & set(calls))}")
    if len(calls) > int(case.get("max_steps", 5)):
        failures.append(f"step limit exceeded: {len(calls)}")
    expected_finish = case.get("expected_finish_reason")
    if expected_finish and result.read_trace["finish_reason"] != expected_finish:
        failures.append(f"finish reason: {result.read_trace['finish_reason']} != {expected_finish}")
    for path in case.get("expected_fact_paths") or []:
        if not _has_path(result.read_facts, path):
            failures.append(f"missing fact path: {path}")
    for term in case.get("required_reply_terms") or []:
        if term not in reply:
            failures.append(f"missing reply term: {term}")
    for claim in case.get("forbidden_claims") or []:
        if claim in reply:
            failures.append(f"forbidden reply claim: {claim}")
    return HarnessResult(case["case_id"], not failures, tuple(calls), reply, tuple(failures))


def _has_path(value: Any, path: str) -> bool:
    current = value
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return False
            current = current[index]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "dataset",
        type=Path,
        nargs="?",
        default=Path("tests/agent/data/controlled_read_cases.json"),
    )
    args = parser.parse_args()
    results = [run_case(case) for case in load_cases(args.dataset)]
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
