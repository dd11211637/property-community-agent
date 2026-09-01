"""Typed domain continuation policy and state projection for a new turn."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from property_agent.agent.announcement_actions import resolve_announcement_followup
from property_agent.agent.announcement_time import (
    resolve_announcement_time_slots,
    trusted_business_date,
)
from property_agent.agent.application.conversation_service import AgentContext
from property_agent.agent.application.runner_signals import (
    ContinuationState,
    build_initial_state,
    explicit_inspection_action,
    explicit_inspection_corrections,
    explicit_repair_corrections,
    first_turn_inspection_signal,
    inspection_group,
    looks_contextual,
    resolve_repair_followup,
)
from property_agent.agent.application.slot_resolution import resolve_inspection_event_facts
from property_agent.agent.application.task_continuation import (
    has_active_inspection_event,
    inspection_event_control,
)
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import (
    AnnouncementDraftingState,
    EmptyWorkingState,
    RepairWorkingState,
    domain_from_legacy,
    project_domain_to_legacy_slots,
)

_INSPECTION_SLOT_GROUPS = {
    "task_query": {"statuses", "assigned_to_me", "limit", "task_id"},
    "task_write": {
        "task_id",
        "expected_version",
        "title",
        "description",
        "point",
        "route_points",
        "note",
        "record_type",
    },
    "event_query": {"event_id", "statuses", "risk_levels", "assigned_to_me", "limit"},
    "event_write": {
        "event_id",
        "expected_version",
        "event_type",
        "risk_level",
        "location",
        "description",
        "note",
        "task_id",
    },
}


@dataclass(frozen=True)
class PreparedStartState:
    state: GraphState
    repair_followup_message: str | None
    immediate_message: str | None = None


def prepare_start_state(
    *,
    conversation_id: str,
    context: AgentContext,
    current_house_id: UUID | None,
    previous: GraphState | None,
    user_text: str,
    slots: dict[str, Any] | None,
) -> PreparedStartState:
    """Apply existing continuation rules and build a new turn state."""
    corrections = _collect_explicit_corrections(user_text, previous)
    roles = tuple(str(role) for role in getattr(context, "roles", ()))
    inspection_override = first_turn_inspection_signal(user_text, roles)
    if inspection_override:
        corrections.update(inspection_override)
    inspection_action = explicit_inspection_action(user_text)
    active_inspection_event = has_active_inspection_event(previous, current_house_id)
    event_control = (
        inspection_event_control(user_text, next_action=inspection_action)
        if active_inspection_event
        else None
    )
    if event_control == "cancel":
        return _cancel_active_task(
            conversation_id=conversation_id,
            context=context,
            current_house_id=current_house_id,
            previous=previous,
            user_text=user_text,
        )
    if inspection_override.get("action") == "report_event":
        corrections.update(resolve_inspection_event_facts({}, user_text))
    active_draft = _active_announcement_draft(previous)
    announcement_followup = resolve_announcement_followup(
        user_text, has_active_draft=active_draft is not None
    )
    announcement_action = (
        announcement_followup.action.value if announcement_followup.action else None
    )
    continuation = _build_continuation(
        previous=previous,
        current_house_id=current_house_id,
        user_text=user_text,
        explicit_corrections=corrections,
    )
    if active_inspection_event and event_control != "switch":
        _continue_inspection_event(continuation, previous, user_text, corrections)
    _apply_inspection_followup(continuation, previous, user_text, inspection_action)
    _apply_announcement_followup(
        continuation, previous, user_text, announcement_action, announcement_followup
    )
    repair_followup, repair_message = resolve_repair_followup(previous, user_text, corrections)
    state = build_initial_state(
        conversation_id=conversation_id,
        context=context,
        current_house_id=current_house_id,
        user_text=user_text,
        slots=slots,
        inspection_override=inspection_override,
        explicit_corrections=corrections,
        continuation=continuation,
        active_draft=active_draft,
        announcement_followup=announcement_followup,
        repair_followup=repair_followup,
    )
    state.add_message("user", user_text)
    if repair_message:
        state.add_message("assistant", repair_message)
    return PreparedStartState(
        state=state,
        repair_followup_message=repair_message,
        immediate_message=repair_message,
    )


def _cancel_active_task(
    *,
    conversation_id: str,
    context: AgentContext,
    current_house_id: UUID | None,
    previous: GraphState | None,
    user_text: str,
) -> PreparedStartState:
    continuation = ContinuationState(
        previous_domain=EmptyWorkingState(),
        legacy_projection={},
        previous_messages=list(previous.messages[-12:]) if previous else [],
        previous_intent=None,
        single_slot_reply={},
        slot_continuation=False,
        contextual_followup=False,
        continuing=True,
    )
    state = build_initial_state(
        conversation_id=conversation_id,
        context=context,
        current_house_id=current_house_id,
        user_text=user_text,
        slots=None,
        inspection_override={},
        explicit_corrections={},
        continuation=continuation,
        active_draft=None,
        announcement_followup=resolve_announcement_followup("", has_active_draft=False),
        repair_followup={},
    )
    message = "已取消本次安防事件上报，不会创建事件。"
    state.add_message("user", user_text)
    state.add_message("assistant", message)
    return PreparedStartState(state, None, message)


def _collect_explicit_corrections(user_text: str, previous: GraphState | None) -> dict[str, str]:
    corrections: dict[str, str] = (
        explicit_repair_corrections(user_text)
        if previous is not None and isinstance(previous.domain, RepairWorkingState)
        else {}
    )
    corrections.update(explicit_inspection_corrections(user_text, previous))
    return corrections


def _active_announcement_draft(previous: GraphState | None) -> dict[str, Any] | None:
    if previous is None or not isinstance(previous.domain, AnnouncementDraftingState):
        return None
    draft = previous.domain
    if not all(getattr(draft, key) is not None for key in ("title", "body", "audience")):
        return None
    return {key: getattr(draft, key) for key in ("title", "body", "audience")}


def _build_continuation(
    *,
    previous: GraphState | None,
    current_house_id: UUID | None,
    user_text: str,
    explicit_corrections: dict[str, str],
) -> ContinuationState:
    same_house = previous is not None and previous.current_house_id == current_house_id
    slot_continuation = bool(
        same_house and previous.missing_slots and previous.pending_action is None
    )
    failed_turn_retry = bool(
        same_house and previous.error and any(marker in user_text for marker in ("重试", "再试"))
    )
    contextual_followup = bool(
        same_house
        and (previous.pending_action is None or explicit_corrections)
        and previous.intent
        and looks_contextual(user_text)
    )
    continuing = slot_continuation or contextual_followup or failed_turn_retry
    previous_messages = list(previous.messages[-12:]) if same_house else []
    previous_domain = EmptyWorkingState()
    legacy_projection: dict[str, Any] = {}
    previous_intent = None
    single_slot_reply: dict[str, Any] = {}
    if continuing and previous is not None:
        previous_domain = previous.domain
        legacy_projection = project_domain_to_legacy_slots(previous.domain)
        previous_intent = previous.intent
        requested_slot = previous.requested_slot or (
            previous.missing_slots[0] if len(previous.missing_slots) == 1 else None
        )
        if slot_continuation and requested_slot and user_text.strip():
            single_slot_reply[requested_slot] = _single_slot_value(requested_slot, user_text)
    return ContinuationState(
        previous_domain=previous_domain,
        legacy_projection=legacy_projection,
        previous_messages=previous_messages,
        previous_intent=previous_intent,
        single_slot_reply=single_slot_reply,
        slot_continuation=slot_continuation,
        contextual_followup=contextual_followup,
        continuing=continuing,
    )


def _single_slot_value(requested_slot: str, user_text: str) -> Any:
    value: Any = user_text.strip()
    if requested_slot != "audience":
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if value == "全社区" else value


def _continue_inspection_event(
    continuation: ContinuationState,
    previous: GraphState | None,
    user_text: str,
    corrections: dict[str, str],
) -> None:
    if previous is None:
        return
    projection = project_domain_to_legacy_slots(previous.domain)
    updates = resolve_inspection_event_facts(
        projection,
        user_text,
        correction=bool(corrections),
    )
    projection.update(updates)
    projection.update(corrections)
    projection["action"] = "report_event"
    projection["target"] = "event"
    continuation.legacy_projection = projection
    continuation.previous_domain = domain_from_legacy("INSPECTION", projection)
    continuation.previous_intent = "INSPECTION"
    continuation.single_slot_reply = {}
    continuation.slot_continuation = True
    continuation.contextual_followup = True
    continuation.continuing = True


def _apply_inspection_followup(
    continuation: ContinuationState,
    previous: GraphState | None,
    user_text: str,
    action: str | None,
) -> None:
    if not action or previous is None:
        return
    group = inspection_group(action, user_text)
    allowed = _INSPECTION_SLOT_GROUPS[group]
    continuation.legacy_projection = {
        key: value for key, value in continuation.legacy_projection.items() if key in allowed
    }
    continuation.legacy_projection["action"] = action
    continuation.legacy_projection["target"] = "event" if group.startswith("event") else "task"
    continuation.previous_domain = domain_from_legacy("INSPECTION", continuation.legacy_projection)
    continuation.previous_intent = "INSPECTION"
    continuation.continuing = True


def _apply_announcement_followup(
    continuation: ContinuationState,
    previous: GraphState | None,
    user_text: str,
    action: str | None,
    followup: Any,
) -> None:
    if not action or previous is None or not previous.domain.kind.startswith("announcement_"):
        return
    if action == "revise":
        previous.pending_action = None
        previous.confirmation_token = None
        previous._interrupt_node = None
    continuation.legacy_projection = project_domain_to_legacy_slots(previous.domain)
    continuation.previous_intent = "ANNOUNCEMENT"
    continuation.legacy_projection["action"] = action
    business_date = trusted_business_date(previous.trusted_context.get("business_date"))
    continuation.legacy_projection.update(resolve_announcement_time_slots(user_text, business_date))
    continuation.legacy_projection.update(followup.slot_updates or {})
    _replace_optional_slot(
        continuation.legacy_projection, "revision_instruction", followup.instruction
    )
    _replace_optional_slot(
        continuation.legacy_projection, "revision_detail_kind", followup.detail_kind
    )
    continuation.previous_domain = domain_from_legacy(
        "ANNOUNCEMENT", continuation.legacy_projection
    )
    continuation.continuing = True
    continuation.contextual_followup = False


def _replace_optional_slot(slots: dict[str, Any], key: str, value: Any) -> None:
    if value:
        slots[key] = value
    else:
        slots.pop(key, None)
