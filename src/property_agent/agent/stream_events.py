"""Typed, bounded internal stream events; no graph state is a public wire contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StreamEventKind(StrEnum):
    TURN_STARTED = "TURN_STARTED"
    PROGRESS = "PROGRESS"
    FINAL = "FINAL"
    FAILED = "FAILED"


class ProgressStage(StrEnum):
    PLANNING = "planning"
    DELEGATING = "delegating"
    EXECUTING_CAPABILITY = "executing_capability"
    WAITING_CONFIRMATION = "waiting_confirmation"
    FINALIZING = "finalizing"


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    kind: StreamEventKind
    runtime_version: str
    data: dict[str, Any] = field(default_factory=dict)
    turn: Any | None = None
    provisional: bool = False

    @classmethod
    def started(cls, conversation_id: str, runtime_version: str, **fields: Any):
        return cls(
            StreamEventKind.TURN_STARTED,
            runtime_version,
            {"conversation_id": conversation_id, **fields},
        )

    @classmethod
    def progress(cls, node: str, runtime_version: str, *, active: bool):
        return cls(
            StreamEventKind.PROGRESS,
            runtime_version,
            {"stage": public_progress_stage(node).value, "active": active},
            provisional=True,
        )

    @classmethod
    def final(cls, turn: Any, runtime_version: str):
        return cls(StreamEventKind.FINAL, runtime_version, turn=turn)

    @classmethod
    def failed(cls, runtime_version: str, category: str):
        return cls(
            StreamEventKind.FAILED,
            runtime_version,
            {"category": category[:64], "recoverable_via_status": True},
        )


def public_progress_stage(node: str) -> ProgressStage:
    value = str(node).lower()
    if "supervisor" in value or "plan" in value:
        return ProgressStage.PLANNING
    if "specialist" in value or "delegate" in value:
        return ProgressStage.DELEGATING
    if "confirm" in value or "interrupt" in value:
        return ProgressStage.WAITING_CONFIRMATION
    if "explain" in value or "finish" in value or "final" in value:
        return ProgressStage.FINALIZING
    return ProgressStage.EXECUTING_CAPABILITY


def coerce_stream_event(event: AgentStreamEvent | tuple[str, Any]) -> AgentStreamEvent:
    """Temporary compatibility seam for test/legacy facade implementations."""
    if isinstance(event, AgentStreamEvent):
        return event
    name, data = event
    if name == "__turn__":
        runtime = getattr(data.conversation, "runtime_version", "unknown")
        return AgentStreamEvent.final(data, runtime)
    if name == "run_started":
        payload = dict(data)
        conversation_id = str(payload.pop("conversation_id", "unknown"))
        return AgentStreamEvent.started(conversation_id, "unknown", **payload)
    return AgentStreamEvent.progress(
        str(dict(data).get("node") or name), "unknown", active=name == "tool_started"
    )


__all__ = ["AgentStreamEvent", "ProgressStage", "StreamEventKind", "coerce_stream_event"]
