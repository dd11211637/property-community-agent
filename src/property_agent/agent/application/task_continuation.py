"""Active business-task guards applied before global semantic planning."""

from __future__ import annotations

from property_agent.agent.orchestration import (
    ObjectiveClassification,
    PlanStatus,
    SpecialistName,
)
from property_agent.agent.planning_contracts import PlanProposal, PlanStepProposal
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import InspectionEventWorkingState

_CANCEL_MARKERS = ("算了", "取消", "不报了", "不用上报", "不要上报")
_SWITCH_MARKERS = ("先不报", "先不管", "改查", "转而", "另外帮我")
_TERMINAL = {
    PlanStatus.COMPLETED,
    PlanStatus.PARTIAL,
    PlanStatus.FAILED,
    PlanStatus.HANDOVER,
}


def has_active_inspection_event(previous: GraphState | None, current_house_id: object) -> bool:
    """Return whether the same-house event task may still accept user facts."""

    if previous is None or previous.current_house_id != current_house_id:
        return False
    domain = previous.domain
    if not isinstance(domain, InspectionEventWorkingState):
        return False
    if domain.action != "report_event":
        return False
    if previous.plan is not None and previous.plan.status in _TERMINAL:
        return False
    return not bool(previous.tool_result and previous.tool_result.get("ok"))


def inspection_event_control(user_text: str, *, next_action: str | None) -> str | None:
    """Classify only explicit cancel/switch controls for an active event task."""

    text = "".join(str(user_text or "").split())
    cancelled = any(marker in text for marker in _CANCEL_MARKERS)
    if not cancelled:
        return None
    explicit_switch = next_action is not None or any(marker in text for marker in _SWITCH_MARKERS)
    return "switch" if explicit_switch else "cancel"


def deterministic_inspection_continuation(state: GraphState) -> PlanProposal | None:
    """Keep an active inspection action inside its selected capability."""

    if state.intent != "INSPECTION":
        return None
    action = state.slots.get("action")
    capability = {
        "report_event": "security_event_create",
        "submit_disposal": "security_event_submit_disposal",
        "start_task": "inspection_start_task",
        "add_record": "inspection_add_record",
        "submit_records": "inspection_submit_records",
    }.get(action)
    if capability is None:
        return None
    fields = _continuation_fields(str(action))
    parameters = {key: state.slots[key] for key in fields if state.slots.get(key) is not None}
    parameters["action"] = action
    return PlanProposal(
        ObjectiveClassification.SINGLE_DOMAIN.value,
        (
            PlanStepProposal(
                step_id=f"inspection-{action}-continuation-v2",
                goal="继续当前巡检业务任务",
                domain="inspection",
                specialist=SpecialistName.INSPECTION.value,
                capability=capability,
                parameters=parameters,
            ),
        ),
        "deterministic-inspection-continuation",
    )


def _continuation_fields(action: str) -> tuple[str, ...]:
    return {
        "report_event": (
            "source_task_id",
            "event_type",
            "risk_level",
            "location",
            "description",
        ),
        "submit_disposal": ("event_id", "expected_version", "note"),
        "start_task": ("task_id", "expected_version"),
        "add_record": ("task_id", "expected_version", "point", "note", "record_type"),
        "submit_records": ("task_id", "expected_version", "point", "note", "record_type"),
    }[action]


__all__ = [
    "deterministic_inspection_continuation",
    "has_active_inspection_event",
    "inspection_event_control",
]
