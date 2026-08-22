"""Immutable trusted runtime facts for one Agent turn.

The runtime wraps the canonical platform request context.  It is deliberately
kept outside checkpointed Agent state: identity, origin and fencing material
must always be reconstructed from current server facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from property_agent.platform.adapters.api.dependencies import RequestContext


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Immutable ceilings and policy inputs for a single execution."""

    max_steps: int = 8
    max_calls: int = 8
    deadline_monotonic: float | None = None
    allowlist: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    trace_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedWrite:
    """Server-issued write material; never authoritative approval truth."""

    confirmation_token: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    approval_ref: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Server-created trusted facts for orchestration and capability adapters."""

    request_context: RequestContext
    conversation_id: str
    current_house_id: UUID | None = None
    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    observation: RuntimeObservation = field(default_factory=RuntimeObservation)
    prepared_write: PreparedWrite | None = field(default=None, repr=False)

    @classmethod
    def from_request_context(
        cls,
        request_context: RequestContext,
        *,
        conversation_id: str,
        current_house_id: UUID | None = None,
        execution_policy: ExecutionPolicy | None = None,
        observation: RuntimeObservation | None = None,
        prepared_write: PreparedWrite | None = None,
    ) -> RuntimeContext:
        return cls(
            request_context=request_context,
            conversation_id=conversation_id,
            current_house_id=current_house_id or getattr(request_context, "current_house_id", None),
            execution_policy=execution_policy or ExecutionPolicy(),
            observation=observation or RuntimeObservation(),
            prepared_write=prepared_write,
        )

    @property
    def actor_id(self) -> UUID:
        return self.request_context.actor_id

    @property
    def community_id(self) -> UUID:
        return self.request_context.community_id

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(str(role) for role in self.request_context.roles)

    @property
    def bound_house_ids(self) -> frozenset[UUID]:
        return frozenset(self.request_context.bound_house_ids)

    @property
    def request_id(self) -> str:
        return self.request_context.request_id

    @property
    def execution_source(self) -> Any:
        return self.request_context.execution_source

    @property
    def agent_lease(self) -> Any:
        return self.request_context.agent_lease

    @property
    def run_id(self) -> Any:
        lease = self.agent_lease
        return getattr(lease, "run_id", None) if lease is not None else None

    @property
    def fence(self) -> Any:
        lease = self.agent_lease
        return getattr(lease, "fence", None) if lease is not None else None

    @property
    def lease_until(self) -> Any:
        lease = self.agent_lease
        return getattr(lease, "lease_until", None) if lease is not None else None

    @property
    def trace_id(self) -> str | None:
        return self.observation.trace_id
