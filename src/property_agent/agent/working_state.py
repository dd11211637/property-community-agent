"""Typed, checkpointable domain working-state variants.

Values here are orchestration candidates only.  Application Services always
reload and re-authorize live business entities and versions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any, Literal, TypeAlias, get_args
from uuid import UUID


@dataclass(slots=True)
class EmptyWorkingState:
    kind: Literal["empty"] = "empty"


@dataclass(slots=True)
class RepairWorkingState:
    kind: Literal["repair"] = "repair"
    action: str | None = None
    description: str | None = None
    location: str | None = None
    urgency: str | None = None
    category: str | None = None
    work_order_id: str | None = None
    statuses: tuple[str, ...] = ()
    limit: int | None = None


@dataclass(slots=True)
class BillingWorkingState:
    kind: Literal["billing"] = "billing"
    action: str | None = None
    query_type: str | None = None
    bill_id: str | None = None
    fee_type: str | None = None
    period: str | None = None
    subject: str | None = None
    description: str | None = None


@dataclass(slots=True)
class AnnouncementQueryState:
    kind: Literal["announcement_query"] = "announcement_query"
    announcement_id: UUID | str | None = None
    statuses: tuple[str, ...] = ()
    target_date: str | None = None
    topic: str | None = None
    limit: int | None = None


@dataclass(slots=True)
class AnnouncementDraftingState:
    kind: Literal["announcement_drafting"] = "announcement_drafting"
    action: str | None = None
    title: str | None = None
    body: str | None = None
    category: str | None = None
    audience: dict[str, Any] | None = None
    topic: str | None = None
    requirements: str | None = None
    revision_instruction: str | None = None
    revision_detail_kind: str | None = None
    target_date: str | None = None
    scheduled_at: datetime | str | None = None


@dataclass(slots=True)
class AnnouncementPublishState:
    kind: Literal["announcement_publish"] = "announcement_publish"
    action: str | None = None
    announcement_id: UUID | str | None = None
    expected_version: int | None = None
    scheduled_at: datetime | str | None = None


@dataclass(slots=True)
class InspectionTaskWorkingState:
    kind: Literal["inspection_task"] = "inspection_task"
    action: str | None = None
    task_id: UUID | str | None = None
    expected_version: int | None = None
    title: str | None = None
    description: str | None = None
    point: str | None = None
    route_points: tuple[str, ...] = ()
    note: str | None = None
    record_type: str | None = None
    finding: str | None = None
    statuses: tuple[str, ...] = ()
    assigned_to_me: bool | None = None
    limit: int | None = None


@dataclass(slots=True)
class InspectionEventWorkingState:
    kind: Literal["inspection_event"] = "inspection_event"
    action: str | None = None
    event_id: UUID | str | None = None
    expected_version: int | None = None
    event_type: str | None = None
    risk_level: str | None = None
    location: str | None = None
    description: str | None = None
    note: str | None = None
    task_id: UUID | str | None = None
    statuses: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
    assigned_to_me: bool | None = None
    limit: int | None = None


DomainWorkingState: TypeAlias = (
    EmptyWorkingState
    | RepairWorkingState
    | BillingWorkingState
    | AnnouncementQueryState
    | AnnouncementDraftingState
    | AnnouncementPublishState
    | InspectionTaskWorkingState
    | InspectionEventWorkingState
)

_BY_KIND = {
    variant.__dataclass_fields__["kind"].default: variant  # type: ignore[attr-defined]
    for variant in get_args(DomainWorkingState)
}

_INTENT_BY_KIND = {
    "repair": "REPAIR",
    "billing": "BILLING",
    "announcement_query": "ANNOUNCEMENT",
    "announcement_drafting": "ANNOUNCEMENT",
    "announcement_publish": "ANNOUNCEMENT",
    "inspection_task": "INSPECTION",
    "inspection_event": "INSPECTION",
}
_DOMAIN_FIELDS = {
    item.name
    for variant in get_args(DomainWorkingState)
    for item in fields(variant)
    if item.name != "kind"
}


class DomainIntentMismatchError(ValueError):
    """A v2 state carries two contradictory domain identities."""


def intent_for_domain(domain: DomainWorkingState) -> str | None:
    return _INTENT_BY_KIND.get(domain.kind)


def validate_domain_intent(intent: str | None, domain: DomainWorkingState) -> None:
    expected = intent_for_domain(domain)
    if expected is None:
        if intent not in {None, "UNCERTAIN", "GENERAL_HELP"}:
            raise DomainIntentMismatchError(
                f"intent {intent!r} requires a typed domain working state"
            )
        return
    if intent not in {None, expected}:
        raise DomainIntentMismatchError(
            f"intent {intent!r} conflicts with domain kind {domain.kind!r}"
        )


def project_domain_to_legacy_slots(
    domain: DomainWorkingState,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project canonical typed state into the legacy graph compatibility shape."""
    if isinstance(domain, EmptyWorkingState):
        return {key: value for key, value in dict(existing or {}).items() if key != "roles"}
    projected = {
        key: value
        for key, value in dict(existing or {}).items()
        if key not in _DOMAIN_FIELDS and key != "roles"
    }
    values = domain_to_dict(domain)
    values.pop("kind", None)
    projected.update(
        {key: value for key, value in values.items() if value is not None and value != ()}
    )
    return projected


def domain_to_dict(domain: DomainWorkingState) -> dict[str, Any]:
    return asdict(domain)


def domain_from_dict(payload: dict[str, Any]) -> DomainWorkingState:
    kind = str(payload.get("kind", "empty"))
    variant = _BY_KIND.get(kind)
    if variant is None:
        raise ValueError(f"unknown domain working-state kind: {kind}")
    allowed = {item.name for item in fields(variant)}
    values = {key: value for key, value in payload.items() if key in allowed}
    for key in ("statuses", "risk_levels", "route_points"):
        if isinstance(values.get(key), list):
            values[key] = tuple(values[key])
    return variant(**values)


def domain_from_legacy(intent: str | None, slots: dict[str, Any]) -> DomainWorkingState:
    normalized = dict(slots)
    if intent == "BILLING":
        if normalized.get("bill_id") == "":
            normalized["bill_id"] = None
        return _construct(BillingWorkingState, normalized)
    if intent == "REPAIR":
        return _construct(RepairWorkingState, normalized)
    if intent == "ANNOUNCEMENT":
        action = normalized.get("action")
        existing_publish = normalized.get("announcement_id") is not None
        if action in {"publish", "schedule", "schedule_publish"} and existing_publish:
            return _construct(AnnouncementPublishState, normalized)
        if action in {"draft", "revise", "create"} or any(
            key in normalized for key in ("title", "body", "audience")
        ):
            return _construct(AnnouncementDraftingState, normalized)
        return _construct(AnnouncementQueryState, normalized)
    if intent == "INSPECTION":
        target = normalized.get("target")
        action = str(normalized.get("action") or "")
        event = target == "event" or "event" in action or "disposal" in action
        return _construct(
            InspectionEventWorkingState if event else InspectionTaskWorkingState, normalized
        )
    return EmptyWorkingState()


def synchronize_typed_domain(state: Any) -> None:
    """Normalize legacy graph output into typed state at an explicit boundary."""
    domain = domain_from_legacy(state.intent, state.slots)
    state.domain = domain
    state.intent = intent_for_domain(domain) or state.intent
    state.slots = project_domain_to_legacy_slots(domain, state.slots)


def _construct(variant: type[DomainWorkingState], payload: dict[str, Any]) -> DomainWorkingState:
    allowed = {item.name for item in fields(variant)} - {"kind"}
    values = {key: value for key, value in payload.items() if key in allowed}
    return domain_from_dict({"kind": variant.__dataclass_fields__["kind"].default, **values})  # type: ignore[attr-defined]
