"""Typed, authority-free contracts for PR5 Supervisor orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from property_agent.agent.capabilities.catalog import default_capability_registry


class ObjectiveClassification(StrEnum):
    SINGLE_DOMAIN = "single-domain"
    MULTI_DOMAIN = "multi-domain"
    GENERAL_HELP = "general-help"
    UNCERTAIN = "uncertain"


class SpecialistName(StrEnum):
    REPAIR = "RepairSpecialist"
    BILLING = "BillingSpecialist"
    ANNOUNCEMENT = "AnnouncementSpecialist"
    INSPECTION = "InspectionSpecialist"


class PlanStatus(StrEnum):
    ACTIVE = "active"
    WAITING_CONFIRMATION = "waiting-confirmation"
    NEEDS_CLARIFICATION = "needs-clarification"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    HANDOVER = "handover"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    PENDING_CONFIRMATION = "pending-confirmation"
    NEEDS_CLARIFICATION = "needs-clarification"
    FAILED = "failed"
    HANDOVER = "handover"


class SpecialistOutcome(StrEnum):
    SUCCESS = "success"
    NEEDS_CLARIFICATION = "needs-clarification"
    REPLAN = "replan"
    HITL_REQUIRED = "hitl-required"
    HANDOVER = "handover"
    DENIED = "denied"
    BUDGET_EXHAUSTED = "budget-exhausted"
    CAPABILITY_ERROR = "capability-error"
    UNSUPPORTED = "unsupported"


class GoalOutcome(StrEnum):
    COMPLETED = "completed"
    PENDING_CONFIRMATION = "pending-confirmation"
    NEEDS_CLARIFICATION = "needs-clarification"
    FAILED = "failed"
    HANDOVER = "handover"


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    domain: str
    specialist: SpecialistName
    goal: str
    dependencies: tuple[str, ...] = ()
    status: PlanStepStatus = PlanStepStatus.PENDING
    capability: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    condition: str | None = None
    result_reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PlanStep:
        return cls(
            step_id=str(value["step_id"]),
            domain=str(value["domain"]),
            specialist=SpecialistName(value["specialist"]),
            goal=str(value["goal"]),
            dependencies=tuple(value.get("dependencies") or ()),
            status=PlanStepStatus(value.get("status", PlanStepStatus.PENDING)),
            capability=value.get("capability"),
            parameters=dict(value.get("parameters") or {}),
            condition=value.get("condition"),
            result_reference=value.get("result_reference"),
        )


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    objective: str
    objective_classification: ObjectiveClassification
    steps: tuple[PlanStep, ...]
    current_step_id: str | None
    status: PlanStatus = PlanStatus.ACTIVE
    replan_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "objective_classification": self.objective_classification.value,
            "steps": [step.to_dict() for step in self.steps],
            "current_step_id": self.current_step_id,
            "status": self.status.value,
            "replan_reason": self.replan_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Plan:
        return cls(
            plan_id=str(value["plan_id"]),
            objective=str(value["objective"]),
            objective_classification=ObjectiveClassification(value["objective_classification"]),
            steps=tuple(PlanStep.from_dict(step) for step in value.get("steps") or ()),
            current_step_id=value.get("current_step_id"),
            status=PlanStatus(value.get("status", PlanStatus.ACTIVE)),
            replan_reason=value.get("replan_reason"),
        )

    def replace_step(self, updated: PlanStep) -> Plan:
        return replace(
            self,
            steps=tuple(
                updated if step.step_id == updated.step_id else step for step in self.steps
            ),
        )


@dataclass(frozen=True, slots=True)
class SpecialistResult:
    outcome: SpecialistOutcome
    step_id: str
    specialist: SpecialistName
    capability: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    public_message: str = ""
    reason_code: str | None = None
    missing_inputs: tuple[str, ...] = ()
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SpecialistResult:
        return cls(
            outcome=SpecialistOutcome(value["outcome"]),
            step_id=str(value["step_id"]),
            specialist=SpecialistName(value["specialist"]),
            capability=value.get("capability"),
            data=dict(value.get("data") or {}),
            public_message=str(value.get("public_message") or ""),
            reason_code=value.get("reason_code"),
            missing_inputs=tuple(value.get("missing_inputs") or ()),
            fingerprint=value.get("fingerprint"),
        )


@dataclass(frozen=True, slots=True)
class OrchestrationBudget:
    started_at_utc: datetime
    deadline_at_utc: datetime
    supervisor_steps: int = 0
    replans: int = 0
    delegations: int = 0
    capability_calls: int = 0
    clarification_loops: int = 0
    cross_domain_steps: int = 0
    fingerprints: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def start(cls, *, now: datetime, duration: timedelta) -> OrchestrationBudget:
        started = _utc(now)
        return cls(started_at_utc=started, deadline_at_utc=started + duration)

    def resume(
        self,
        *,
        now: datetime,
        server_ceiling: timedelta,
    ) -> OrchestrationBudget:
        effective = min(self.deadline_at_utc, _utc(now) + server_ceiling)
        return replace(self, deadline_at_utc=effective)

    def expired(self, now: datetime) -> bool:
        return _utc(now) >= self.deadline_at_utc

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["started_at_utc"] = self.started_at_utc.isoformat()
        value["deadline_at_utc"] = self.deadline_at_utc.isoformat()
        value["fingerprints"] = sorted(self.fingerprints)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OrchestrationBudget:
        return cls(
            started_at_utc=_parse_utc(value["started_at_utc"]),
            deadline_at_utc=_parse_utc(value["deadline_at_utc"]),
            supervisor_steps=int(value.get("supervisor_steps", 0)),
            replans=int(value.get("replans", 0)),
            delegations=int(value.get("delegations", 0)),
            capability_calls=int(value.get("capability_calls", 0)),
            clarification_loops=int(value.get("clarification_loops", 0)),
            cross_domain_steps=int(value.get("cross_domain_steps", 0)),
            fingerprints=frozenset(value.get("fingerprints") or ()),
        )


class PlanValidator:
    """Validate model/deterministic plan proposals against server-owned routing facts."""

    _DOMAIN_SPECIALISTS = {
        "repair": SpecialistName.REPAIR,
        "billing": SpecialistName.BILLING,
        "announcement": SpecialistName.ANNOUNCEMENT,
        "inspection": SpecialistName.INSPECTION,
    }

    def __init__(self, *, max_steps: int = 8) -> None:
        self._max_steps = max_steps
        registry = default_capability_registry()
        self._capability_domains = {spec.name: spec.domain for spec in registry.inventory()}

    def validate(self, plan: Plan, *, global_intent: str | None = None) -> Plan:
        del global_intent  # top-level context is deliberately not step execution authority
        if not plan.plan_id or not plan.objective.strip():
            raise ValueError("plan identity and objective are required")
        if len(plan.steps) > self._max_steps:
            raise ValueError("plan exceeds maximum step count")
        if not plan.steps and plan.objective_classification not in {
            ObjectiveClassification.GENERAL_HELP,
            ObjectiveClassification.UNCERTAIN,
        }:
            raise ValueError("executable plan requires at least one step")
        by_id = {step.step_id: step for step in plan.steps}
        if len(by_id) != len(plan.steps):
            raise ValueError("plan step identifiers must be unique")
        if plan.current_step_id is not None and plan.current_step_id not in by_id:
            raise ValueError("current plan step is unknown")
        for step in plan.steps:
            self._validate_step(step, by_id)
        self._reject_cycles(by_id)
        return plan

    def _validate_step(self, step: PlanStep, by_id: dict[str, PlanStep]) -> None:
        expected = self._DOMAIN_SPECIALISTS.get(step.domain)
        if expected is None:
            raise ValueError(f"unknown step domain: {step.domain}")
        if step.specialist != expected:
            raise ValueError("step specialist does not match its domain")
        if step.capability is not None:
            capability_domain = self._capability_domains.get(step.capability)
            if capability_domain != step.domain:
                raise ValueError("step capability does not match its domain")
        if any(dependency not in by_id for dependency in step.dependencies):
            raise ValueError("step dependency is unknown")
        if step.step_id in step.dependencies:
            raise ValueError("cyclic plan dependency")

    @staticmethod
    def _reject_cycles(by_id: dict[str, PlanStep]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("cyclic plan dependency")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in by_id[step_id].dependencies:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in by_id:
            visit(step_id)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("budget timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return _utc(parsed)
