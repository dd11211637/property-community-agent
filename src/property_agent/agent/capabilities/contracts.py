"""Typed orchestration contracts for Agent business capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict


class CapabilityInput(BaseModel):
    """Base input that rejects undeclared, model-controlled authority fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityOutput(BaseModel):
    """Base output validated before it returns to orchestration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


InputT = TypeVar("InputT", bound=CapabilityInput)
OutputT = TypeVar("OutputT", bound=CapabilityOutput)


class CapabilityRisk(StrEnum):
    READ = "read"
    WRITE_LOW_RISK = "write-low-risk"
    WRITE_HIGH_RISK = "write-high-risk"


class ApprovalPosture(StrEnum):
    """Static declaration only; effective approval is a policy decision."""

    NONE = "none"
    POLICY = "policy"
    HUMAN_ONLY = "human-only"


class PolicyDisposition(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    HUMAN_ONLY = "human-only"


class ApprovalRequirement(StrEnum):
    NONE = "none"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class CapabilityPresentation:
    title: str
    confirmation_title: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Canonical static metadata; contains no callable or live business rule."""

    name: str
    domain: str
    description: str
    input_type: type[CapabilityInput]
    output_type: type[CapabilityOutput]
    baseline_risk: CapabilityRisk
    approval_posture: ApprovalPosture
    presentation: CapabilityPresentation
    tags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError("capability name must be a stable snake_case identifier")
        if not self.domain.strip() or not self.description.strip():
            raise ValueError("capability domain and description are required")


@dataclass(frozen=True, slots=True)
class CapabilityWriteContext:
    """Server-issued material bound to a prepared write invocation."""

    confirmation_token: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    approval_ref: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeContext:
    """Server-created facts supplied separately from model-controlled input."""

    request_context: Any
    current_house_id: Any = None
    legacy_state: Any = None
    write: CapabilityWriteContext | None = field(default=None, repr=False)
    trusted_runtime: Any = field(default=None, repr=False)
    inspection_context_projector: Any = field(default=None, repr=False)

    @property
    def actor_id(self) -> Any:
        return getattr(self.request_context, "actor_id", None)

    @property
    def community_id(self) -> Any:
        return getattr(self.request_context, "community_id", None)

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(getattr(self.request_context, "roles", ()))

    @property
    def execution_policy(self) -> Any:
        from property_agent.agent.runtime import ExecutionPolicy

        if self.trusted_runtime is None:
            return ExecutionPolicy()
        return self.trusted_runtime.execution_policy


@dataclass(slots=True)
class CapabilityInvocationState:
    """Mutable, checkpointable progress for one capability invocation."""

    step: int = 0
    calls_made: int = 0
    prior_fingerprints: frozenset[str] = field(default_factory=frozenset)
    fingerprint: str | None = None
    selected_capability: str | None = None
    retry_count: int = 0
    human_confirmed: bool = False

    def __post_init__(self) -> None:
        self.prior_fingerprints = frozenset(self.prior_fingerprints)


@dataclass(frozen=True, slots=True)
class CapabilityPolicyDecision:
    disposition: PolicyDisposition
    effective_risk: CapabilityRisk
    approval_requirement: ApprovalRequirement
    reason_code: str


@dataclass(frozen=True, slots=True)
class CapabilityError:
    code: str
    message: str
    kind: str
    details: dict[str, Any] = field(default_factory=dict)
    cause: Exception | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    capability: str
    decision: CapabilityPolicyDecision | None
    output: CapabilityOutput | None = None
    error: CapabilityError | None = None
    fingerprint: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.output is not None


class CapabilityDomainError(RuntimeError):
    """Adapter translation error that preserves a public business error contract."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class CapabilityAdapter(Protocol):
    def __call__(
        self, request: CapabilityInput, runtime: CapabilityRuntimeContext
    ) -> CapabilityOutput: ...
