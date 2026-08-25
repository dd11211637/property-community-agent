"""Typed checkpointable Agent state with a legacy GraphState facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict
from uuid import UUID

from property_agent.agent.capabilities.contracts import CapabilityInvocationState
from property_agent.agent.memory_contracts import MemoryContext
from property_agent.agent.orchestration import (
    GoalOutcome,
    OrchestrationBudget,
    Plan,
    SpecialistResult,
)
from property_agent.agent.working_state import DomainWorkingState, EmptyWorkingState


class AgentMessage(TypedDict, total=False):
    role: str
    content: str
    name: str


@dataclass(slots=True)
class ClarificationState:
    missing_inputs: list[str] = field(default_factory=list)
    requested_input: str | None = None


@dataclass(slots=True)
class ProposedAction:
    capability: str
    params: dict[str, Any]
    params_hash: str | None = None
    issued_at: str | None = None


@dataclass(slots=True)
class OrchestrationState:
    resume: Any | None = None
    interrupt_node: str | None = None
    continuation: bool = False
    contextual_followup: bool = False


@dataclass
class AgentState:
    """Mutable orchestration state; never a trusted identity or approval source.

    ``slots`` and identity fields are version-1 graph compatibility projections.
    New capabilities consume typed input plus trusted ``RuntimeContext``.
    """

    conversation_id: str
    schema_version: int = 2
    domain: DomainWorkingState = field(default_factory=EmptyWorkingState)
    capability_invocation: CapabilityInvocationState = field(
        default_factory=CapabilityInvocationState
    )
    clarification: ClarificationState = field(default_factory=ClarificationState)
    proposed_action: ProposedAction | None = None
    orchestration: OrchestrationState = field(default_factory=OrchestrationState)
    plan: Plan | None = None
    orchestration_budget: OrchestrationBudget | None = None
    specialist_results: tuple[SpecialistResult, ...] = ()
    goal_outcomes: dict[str, GoalOutcome] = field(default_factory=dict)
    retrieved_memories: MemoryContext = field(default_factory=MemoryContext)
    actor_id: UUID | None = None
    community_id: UUID | None = None
    current_house_id: UUID | None = None
    intent: str | None = None
    confidence: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    requested_slot: str | None = None
    operation_level: str | None = None
    pending_action: dict[str, Any] | None = None
    confirmation_token: str | None = None
    approval_ref: str | None = None
    tool_result: dict[str, Any] | None = None
    retry_count: int = 0
    handover_required: bool = False
    messages: list[AgentMessage] = field(default_factory=list)
    trusted_context: dict[str, Any] = field(default_factory=dict)
    read_facts: dict[str, Any] | None = None
    read_trace: dict[str, Any] | None = None
    error: str | None = None
    # Constructor-compatible v1 projection. The codec maps these fields to the
    # typed ``orchestration`` owner on every persistence boundary.
    _resume: Any | None = None
    _interrupt_node: str | None = None
    _continuation: bool = False
    _contextual_followup: bool = False

    def add_message(self, role: str, content: str, **extra: Any) -> None:
        self.messages.append({"role": role, "content": content, **extra})

    def to_dict(self) -> dict[str, Any]:
        from property_agent.agent.state_codec import CheckpointStateCodec

        return CheckpointStateCodec().encode(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentState:
        from property_agent.agent.state_codec import CheckpointStateCodec

        return CheckpointStateCodec().decode(data)


GraphState = AgentState
