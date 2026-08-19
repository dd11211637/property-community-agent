"""Agent 运行接入层。

AgentHarnessPort 面向两类实现：
- RecordedHarness：回放 runs/ 下已录制的运行（离线评测）。
- 真实 harness：调用 property_agent 的 AgentSessionRunner（联机评测，
  由调用方自行装配 graph / 会话 / 确认 Port，评测系统不耦合其依赖）。
"""

from __future__ import annotations

from typing import Any, Protocol

from judge.schemas import AgentRun, CaseInput, TraceEvent


class AgentHarnessPort(Protocol):
    """把评测输入变成一次可评估的 Agent 运行。"""

    def run(self, case_id: str, case_input: CaseInput) -> AgentRun:  # pragma: no cover
        ...


def record_run(
    turn: Any,
    case_id: str,
    agent_mode: str = "keyword",
    *,
    degraded: bool = False,
) -> dict[str, Any]:
    """把真实 AgentTurn（property_agent.agent.application.runner）转录为运行记录。

    事件序列与图执行顺序一致：意图 → 槽位请求/填充 → 工具调用与结果 →
    确认/转人工 → 最终回复。只读规划步数从 read_trace 提取（平台约束 ≤5）。
    转录不修改任何状态，纯读取。
    """
    state = turn.state
    events: list[TraceEvent] = []

    def emit(event_type: str, name: str = "", **kwargs: Any) -> None:
        events.append(TraceEvent(step=len(events) + 1, type=event_type, name=name, **kwargs))

    if getattr(state, "intent", None):
        emit("intent", name=str(state.intent), params={"confidence": state.confidence})
    if state.requested_slot:
        emit("slot_request", name=state.requested_slot)
    for key, value in (state.slots or {}).items():
        emit("slot_set", name=str(key), params={"value": _plain(value)})

    tool_result = state.tool_result or {}
    if tool_result.get("tool"):
        emit("tool_call", name=str(tool_result["tool"]))
        emit(
            "tool_result",
            name=str(tool_result["tool"]),
            ok=bool(tool_result.get("ok", False)),
            params=_plain(tool_result.get("data", {})),
        )

    read_trace = state.read_trace or {}
    for step in read_trace.get("steps", []):
        emit("tool_call", name=str(step.get("tool", "")))
        emit(
            "tool_result",
            name=str(step.get("tool", "")),
            ok=bool(step.get("ok", False)),
            params={"summary": str(step.get("summary", ""))},
        )

    if state.confirmation_token:
        emit("confirmation_granted")
    if state.handover_required:
        emit("handover", detail="high risk operation")

    reply = turn.reply()
    if reply:
        emit("reply", detail=reply)

    run = AgentRun(
        case_id=case_id,
        agent_mode="deepseek" if agent_mode == "deepseek" else "keyword",
        events=events,
        final_answer=reply,
        handover_required=bool(state.handover_required),
        degraded=degraded,
    )
    return run.model_dump(mode="json")


def _plain(value: Any) -> Any:
    """UUID / 枚举等转 JSON 友好表示。"""
    if hasattr(value, "hex") and not isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
