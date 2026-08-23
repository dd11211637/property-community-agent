"""Pure versioned checkpoint serialization and legacy conversion."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any
from uuid import UUID

from property_agent.agent.capabilities.contracts import CapabilityInvocationState
from property_agent.agent.state import (
    AgentState,
    ClarificationState,
    OrchestrationState,
    ProposedAction,
)
from property_agent.agent.working_state import (
    domain_from_dict,
    domain_from_legacy,
    domain_to_dict,
    intent_for_domain,
    project_domain_to_legacy_slots,
    validate_domain_intent,
)


class CheckpointDecodeError(ValueError):
    """Malformed or unsupported checkpoint; callers must fail closed."""


class CheckpointStateCodec:
    CURRENT_VERSION = 2

    def encode(self, state: AgentState) -> dict[str, Any]:
        domain = state.domain
        validate_domain_intent(state.intent, domain)
        intent = intent_for_domain(domain) or state.intent
        legacy_slots = project_domain_to_legacy_slots(domain, state.slots)
        clarification = ClarificationState(
            missing_inputs=list(state.missing_slots),
            requested_input=state.requested_slot,
        )
        proposed = (
            self._proposed_from_legacy(state.pending_action)
            if state.pending_action is not None
            else state.proposed_action
        )
        orchestration = OrchestrationState(
            resume=state._resume,
            interrupt_node=state._interrupt_node,
            continuation=state._continuation,
            contextual_followup=state._contextual_followup,
        )
        invocation = replace(
            state.capability_invocation,
            selected_capability=str(legacy_slots.get("tool"))
            if legacy_slots.get("tool")
            else state.capability_invocation.selected_capability,
            retry_count=state.retry_count,
        )
        return {
            "schema_version": self.CURRENT_VERSION,
            "conversation_id": state.conversation_id,
            "domain": domain_to_dict(domain),
            "capability_invocation": asdict(invocation),
            "clarification": asdict(clarification),
            "proposed_action": asdict(proposed) if proposed else None,
            "orchestration": asdict(orchestration),
            "actor_id": self._uuid(state.actor_id),
            "community_id": self._uuid(state.community_id),
            "current_house_id": self._uuid(state.current_house_id),
            "intent": intent,
            "confidence": state.confidence,
            "slots": deepcopy(legacy_slots),
            "missing_slots": list(state.missing_slots),
            "requested_slot": state.requested_slot,
            "operation_level": state.operation_level,
            "pending_action": deepcopy(state.pending_action),
            "confirmation_token": state.confirmation_token,
            "approval_ref": state.approval_ref,
            "tool_result": deepcopy(state.tool_result),
            "retry_count": state.retry_count,
            "handover_required": state.handover_required,
            "messages": deepcopy(state.messages),
            "trusted_context": deepcopy(state.trusted_context),
            "read_facts": deepcopy(state.read_facts),
            "read_trace": deepcopy(state.read_trace),
            "error": state.error,
        }

    def decode(self, raw: dict[str, Any]) -> AgentState:
        payload = deepcopy(raw)
        try:
            version = int(payload.get("schema_version", 1))
        except (TypeError, ValueError) as exc:
            raise CheckpointDecodeError("invalid checkpoint schema version") from exc
        if version == 1:
            return self._decode_legacy(payload)
        if version != self.CURRENT_VERSION:
            raise CheckpointDecodeError(f"unsupported checkpoint schema version: {version}")
        return self._decode_current(payload)

    def _decode_current(self, payload: dict[str, Any]) -> AgentState:
        invocation = CapabilityInvocationState(**dict(payload.get("capability_invocation") or {}))
        clarification = ClarificationState(**dict(payload.get("clarification") or {}))
        orchestration = OrchestrationState(**dict(payload.get("orchestration") or {}))
        proposed_raw = payload.get("proposed_action")
        proposed = ProposedAction(**proposed_raw) if proposed_raw else None
        domain = domain_from_dict(dict(payload.get("domain") or {"kind": "empty"}))
        validate_domain_intent(payload.get("intent"), domain)
        return self._base_state(
            payload,
            domain=domain,
            invocation=invocation,
            clarification=clarification,
            orchestration=orchestration,
            proposed=proposed,
        )

    def _decode_legacy(self, payload: dict[str, Any]) -> AgentState:
        conversation_id = payload.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise CheckpointDecodeError("legacy checkpoint has no conversation_id")
        slots = payload.get("slots") or {}
        if not isinstance(slots, dict):
            raise CheckpointDecodeError("legacy checkpoint slots must be an object")
        orchestration = OrchestrationState(
            resume=payload.get("_resume"),
            interrupt_node=payload.get("_interrupt_node"),
            continuation=bool(payload.get("_continuation", False)),
            contextual_followup=bool(payload.get("_contextual_followup", False)),
        )
        pending = payload.get("pending_action")
        return self._base_state(
            payload,
            domain=domain_from_legacy(payload.get("intent"), slots),
            invocation=CapabilityInvocationState(
                selected_capability=str(slots.get("tool")) if slots.get("tool") else None,
                retry_count=int(payload.get("retry_count", 0)),
            ),
            clarification=ClarificationState(
                missing_inputs=list(payload.get("missing_slots") or []),
                requested_input=payload.get("requested_slot"),
            ),
            orchestration=orchestration,
            proposed=self._proposed_from_legacy(pending),
        )

    def _base_state(
        self,
        payload: dict[str, Any],
        *,
        domain: Any,
        invocation: CapabilityInvocationState,
        clarification: ClarificationState,
        orchestration: OrchestrationState,
        proposed: ProposedAction | None,
    ) -> AgentState:
        return AgentState(
            conversation_id=str(payload["conversation_id"]),
            schema_version=self.CURRENT_VERSION,
            domain=domain,
            capability_invocation=invocation,
            clarification=clarification,
            proposed_action=proposed,
            orchestration=orchestration,
            actor_id=self._decode_uuid(payload.get("actor_id")),
            community_id=self._decode_uuid(payload.get("community_id")),
            current_house_id=self._decode_uuid(payload.get("current_house_id")),
            intent=payload.get("intent"),
            confidence=float(payload.get("confidence", 0.0)),
            slots=project_domain_to_legacy_slots(
                domain,
                dict(payload.get("slots") or {}),
            ),
            missing_slots=list(payload.get("missing_slots") or []),
            requested_slot=payload.get("requested_slot"),
            operation_level=payload.get("operation_level"),
            pending_action=deepcopy(payload.get("pending_action")),
            confirmation_token=payload.get("confirmation_token"),
            approval_ref=payload.get("approval_ref"),
            tool_result=deepcopy(payload.get("tool_result")),
            retry_count=invocation.retry_count,
            handover_required=bool(payload.get("handover_required", False)),
            messages=list(payload.get("messages") or []),
            trusted_context=dict(payload.get("trusted_context") or {}),
            read_facts=deepcopy(payload.get("read_facts")),
            read_trace=deepcopy(payload.get("read_trace")),
            error=payload.get("error"),
            _resume=orchestration.resume,
            _interrupt_node=orchestration.interrupt_node,
            _continuation=orchestration.continuation,
            _contextual_followup=orchestration.contextual_followup,
        )

    @staticmethod
    def _proposed_from_legacy(pending: Any) -> ProposedAction | None:
        if not isinstance(pending, dict) or not pending.get("tool"):
            return None
        return ProposedAction(
            capability=str(pending["tool"]),
            params=dict(pending.get("params") or {}),
            params_hash=pending.get("params_hash"),
            issued_at=pending.get("issued_at"),
        )

    @staticmethod
    def _uuid(value: UUID | None) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _decode_uuid(value: Any) -> UUID | None:
        if value in (None, ""):
            return None
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise CheckpointDecodeError("invalid checkpoint identity/scope UUID") from exc
