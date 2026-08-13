"""Provider-neutral contracts for the bounded, read-only agent loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class PlannerAction(StrEnum):
    CALL_TOOL = "CALL_TOOL"
    FINAL = "FINAL"
    CLARIFY = "CLARIFY"
    HANDOVER = "HANDOVER"


class ReadRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class PlannerDecision:
    action: PlannerAction
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    reason_code: str = "UNSPECIFIED"
    answer_goal: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PlannerDecision:
        if not isinstance(value, dict):
            raise ValueError("planner decision must be an object")
        expected = {"action", "tool", "arguments", "reason_code", "answer_goal"}
        if set(value) - expected:
            raise ValueError("planner decision contains unsupported fields")
        action = PlannerAction(str(value.get("action") or ""))
        arguments = value.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("planner arguments must be an object")
        tool = value.get("tool")
        if action == PlannerAction.CALL_TOOL and not isinstance(tool, str):
            raise ValueError("CALL_TOOL requires a tool name")
        return cls(
            action=action,
            tool=tool if isinstance(tool, str) else None,
            arguments=arguments,
            reason_code=str(value.get("reason_code") or "UNSPECIFIED")[:64],
            answer_goal=(str(value["answer_goal"])[:128] if value.get("answer_goal") else None),
        )


@dataclass(frozen=True, slots=True)
class ReadToolSpec:
    name: str
    description: str
    allowed_arguments: frozenset[str]
    required_arguments: frozenset[str] = frozenset()
    max_result_records: int = 20

    def public_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_arguments": sorted(self.allowed_arguments),
            "required_arguments": sorted(self.required_arguments),
        }


@dataclass(frozen=True, slots=True)
class Observation:
    tool: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    source: str = "application_service"
    step: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentTrace:
    trace_id: str
    status: ReadRunStatus = ReadRunStatus.RUNNING
    events: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    degraded: bool = False

    def add(self, event_type: str, **detail: Any) -> None:
        self.events.append({"type": event_type, **detail})

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "status": self.status.value,
            "events": self.events,
            "finish_reason": self.finish_reason,
            "degraded": self.degraded,
            "step_count": sum(1 for event in self.events if event["type"] == "tool_call"),
        }


@dataclass(frozen=True, slots=True)
class FactsPackage:
    query: str
    observations: tuple[Observation, ...]
    trace: AgentTrace

    def to_dict(self) -> dict[str, Any]:
        successful = [item for item in self.observations if item.ok]
        records: list[dict[str, Any]] = []
        for item in successful:
            data = item.data
            if isinstance(data.get("items"), list):
                records.extend(record for record in data["items"] if isinstance(record, dict))
            for key in ("work_order", "bill", "announcement"):
                if isinstance(data.get(key), dict):
                    records.append(data[key])
        return {
            "query": self.query,
            "observations": [item.to_dict() for item in self.observations],
            "records": records,
            "trace": self.trace.to_dict(),
        }
