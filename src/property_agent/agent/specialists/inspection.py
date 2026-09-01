"""Stateless Inspection and Security specialist."""

from property_agent.agent.orchestration import (
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.specialists.base import StatelessSpecialist


class InspectionSpecialist(StatelessSpecialist):
    name = SpecialistName.INSPECTION
    domain = "inspection"

    def choose_capability(self, step, state, prior_results):
        del step, prior_results
        action = state.slots.get("action")
        return {
            "get_task": "inspection_get_task",
            "get_event": "inspection_get_event",
            "create": "inspection_create",
            "start_task": "inspection_start_task",
            "add_record": "inspection_add_record",
            "submit_records": "inspection_submit_records",
            "ai_suggest": "inspection_ai_suggest",
            "report_event": "security_event_create",
            "submit_disposal": "security_event_submit_disposal",
            "close_high_risk": "close_high_risk_event",
        }.get(action, "inspection_list")

    def project_parameters(self, capability, step, state, prior_results):
        del prior_results
        values = {**state.slots, **step.parameters}
        fields = {
            "inspection_list": (
                "target",
                "statuses",
                "risk_levels",
                "assigned_to_me",
                "limit",
            ),
            "inspection_get_task": ("task_id",),
            "inspection_get_event": ("event_id",),
            "inspection_create": (
                "title",
                "description",
                "point",
                "route_points",
                "planned_at",
                "due_at",
            ),
            "inspection_start_task": ("task_id", "expected_version"),
            "inspection_add_record": (
                "task_id",
                "expected_version",
                "point",
                "note",
                "record_type",
                "is_supplement",
                "actual_time",
                "supplement_reason",
            ),
            "inspection_submit_records": (
                "task_id",
                "expected_version",
                "point",
                "note",
                "record_type",
                "is_supplement",
                "actual_time",
                "supplement_reason",
            ),
            "inspection_ai_suggest": ("task_id", "point", "finding", "severity", "model"),
            "security_event_create": (
                "source_task_id",
                "event_type",
                "risk_level",
                "location",
                "description",
            ),
            "security_event_submit_disposal": ("event_id", "expected_version", "note"),
            "close_high_risk_event": ("event_id",),
        }[capability]
        projected = {key: values[key] for key in fields if values.get(key) is not None}
        if capability == "inspection_create":
            projected["route_points"] = tuple(projected.get("route_points") or ())
        if capability == "inspection_list":
            projected.update(
                target=projected.get("target") or "task",
                statuses=tuple(projected.get("statuses") or ()),
                risk_levels=tuple(projected.get("risk_levels") or ()),
                assigned_to_me=bool(projected.get("assigned_to_me", False)),
                limit=int(projected.get("limit") or 20),
            )
        return projected

    def interpret_error(self, step, capability, parameters, params_hash, result):
        error = result.error
        not_found_codes = {
            "TASK_NOT_FOUND",
            "EVENT_NOT_FOUND",
            "INSPECTION_TASK_NOT_FOUND",
            "SECURITY_EVENT_NOT_FOUND",
        }
        if capability in {"inspection_get_task", "inspection_get_event"} and (
            error and error.code in not_found_codes
        ):
            target = "event" if capability == "inspection_get_event" else "task"
            retained = {
                key: value
                for key, value in step.parameters.items()
                if key in {"statuses", "risk_levels", "assigned_to_me", "limit"}
                and value is not None
            }
            return SpecialistResult(
                SpecialistOutcome.REPLAN,
                step.step_id,
                self.name,
                capability=capability,
                data={
                    "replacement_capability": "inspection_list",
                    "replacement_parameters": {"target": target, "limit": 20, **retained},
                },
                public_message="未找到指定对象，将改用受限列表查询。",
                reason_code=error.code,
                fingerprint=result.fingerprint,
            )
        return super().interpret_error(step, capability, parameters, params_hash, result)
