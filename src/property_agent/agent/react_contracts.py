"""Strict, authority-free contracts for observation-driven ReAct goals."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ReactDecisionType(StrEnum):
    ACT = "ACT"
    CLARIFY = "CLARIFY"
    FINISH = "FINISH"
    HANDOVER = "HANDOVER"


class GoalStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    HANDOVER = "HANDOVER"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_FORBIDDEN_ARGUMENTS = frozenset(
    {
        "actor_id",
        "community_id",
        "house_id",
        "current_house_id",
        "roles",
        "confirmation_token",
        "approval_ref",
        "idempotency_key",
        "database",
        "request_context",
        "lease",
        "fence",
    }
)


@dataclass(frozen=True, slots=True)
class ReactDecision:
    decision: ReactDecisionType
    goal_status: GoalStatus
    capability: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    missing_information: tuple[str, ...] = ()
    question: str | None = None
    reason_code: str = ""
    rationale_summary: str = ""
    requested_domain: str | None = None

    def __post_init__(self) -> None:
        if set(self.arguments) & _FORBIDDEN_ARGUMENTS:
            raise ValueError("ReAct arguments contain server-owned authority fields")
        if self.decision is ReactDecisionType.ACT:
            if not self.capability or self.missing_information or self.question:
                raise ValueError("ACT requires only capability and arguments")
            if self.goal_status is not GoalStatus.IN_PROGRESS:
                raise ValueError("ACT requires IN_PROGRESS goal status")
        elif self.decision is ReactDecisionType.CLARIFY:
            if not self.missing_information or not self.question or self.capability:
                raise ValueError("CLARIFY requires missing information and question")
            if self.goal_status is not GoalStatus.NEEDS_CLARIFICATION:
                raise ValueError("CLARIFY requires NEEDS_CLARIFICATION goal status")
        elif self.capability or self.arguments or self.missing_information or self.question:
            raise ValueError("terminal ReAct decision cannot contain action fields")
        if self.decision is ReactDecisionType.FINISH and self.goal_status not in {
            GoalStatus.COMPLETED,
            GoalStatus.PARTIAL,
        }:
            raise ValueError("FINISH requires COMPLETED or PARTIAL goal status")
        if (
            self.decision is ReactDecisionType.HANDOVER
            and self.goal_status is not GoalStatus.HANDOVER
        ):
            raise ValueError("HANDOVER decision requires HANDOVER goal status")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReactDecision:
        allowed = {
            "decision",
            "goal_status",
            "capability",
            "arguments",
            "missing_information",
            "question",
            "reason_code",
            "rationale_summary",
            "requested_domain",
        }
        if set(value) - allowed:
            raise ValueError("ReAct decision contains unknown fields")
        return cls(
            decision=ReactDecisionType(value["decision"]),
            goal_status=GoalStatus(value["goal_status"]),
            capability=value.get("capability"),
            arguments=dict(value.get("arguments") or {}),
            missing_information=tuple(value.get("missing_information") or ()),
            question=value.get("question"),
            reason_code=str(value.get("reason_code") or ""),
            rationale_summary=str(value.get("rationale_summary") or "")[:240],
            requested_domain=value.get("requested_domain"),
        )

    def trace_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "goal_status": self.goal_status.value,
            "capability": self.capability,
            "reason_code": self.reason_code,
            "requested_domain": self.requested_domain,
        }

    def checkpoint_dict(self) -> dict[str, Any]:
        return {
            **self.trace_dict(),
            "arguments": self.arguments,
            "missing_information": list(self.missing_information),
            "question": self.question,
            "rationale_summary": "",
        }


@dataclass(frozen=True, slots=True)
class ReactObservation:
    capability: str
    ok: bool
    params_hash: str
    result_fingerprint: str
    error_code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReactObservation:
        return cls(
            capability=str(value["capability"]),
            ok=bool(value["ok"]),
            params_hash=str(value["params_hash"]),
            result_fingerprint=str(value["result_fingerprint"]),
            error_code=value.get("error_code"),
            data=dict(value.get("data") or {}),
        )


@dataclass(slots=True)
class ActiveGoalState:
    goal_id: str
    goal: str
    domain: str
    status: GoalStatus = GoalStatus.IN_PROGRESS
    candidate_facts: dict[str, Any] = field(default_factory=dict)
    observations: tuple[ReactObservation, ...] = ()
    missing_information: tuple[str, ...] = ()
    last_action: str | None = None
    action_count: int = 0
    clarification_count: int = 0
    domain_transition_count: int = 0
    handover: bool = False
    degraded: bool = False
    fallback_used: bool = False
    last_decision: ReactDecision | None = None

    def append_observation(self, observation: ReactObservation) -> None:
        self.observations = (*self.observations, observation)[-12:]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["observations"] = [item.to_dict() for item in self.observations]
        value["last_decision"] = (
            self.last_decision.checkpoint_dict() if self.last_decision else None
        )
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActiveGoalState:
        decision = value.get("last_decision")
        return cls(
            goal_id=str(value["goal_id"]),
            goal=str(value["goal"]),
            domain=str(value["domain"]),
            status=GoalStatus(value.get("status", GoalStatus.IN_PROGRESS)),
            candidate_facts=dict(value.get("candidate_facts") or {}),
            observations=tuple(
                ReactObservation.from_dict(v) for v in value.get("observations") or ()
            ),
            missing_information=tuple(value.get("missing_information") or ()),
            last_action=value.get("last_action"),
            action_count=int(value.get("action_count", 0)),
            clarification_count=int(value.get("clarification_count", 0)),
            domain_transition_count=int(value.get("domain_transition_count", 0)),
            handover=bool(value.get("handover", False)),
            degraded=bool(value.get("degraded", False)),
            fallback_used=bool(value.get("fallback_used", False)),
            last_decision=ReactDecision.from_dict(decision) if decision else None,
        )
