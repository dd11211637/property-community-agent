"""Deterministic governance for model-proposed ReAct actions."""

from __future__ import annotations

from typing import Any

from property_agent.agent.orchestration import SpecialistName
from property_agent.platform.application.hashing import canonical_hash

DOMAIN_ALLOWLISTS = {
    "repair": frozenset({"repair_list", "repair_get", "repair_create"}),
    "billing": frozenset({"billing_query", "billing_consult"}),
    "announcement": frozenset(
        {
            "announcement_list",
            "announcement_get",
            "community_knowledge_search",
            "announcement_draft",
            "announcement_revise",
            "announcement_create_draft",
            "announce_publish",
            "announcement_schedule_publish",
        }
    ),
    "inspection": frozenset(
        {
            "inspection_list",
            "inspection_get_task",
            "inspection_get_event",
            "inspection_create",
            "inspection_start_task",
            "inspection_add_record",
            "inspection_submit_records",
            "inspection_ai_suggest",
            "security_event_create",
            "security_event_submit_disposal",
            "close_high_risk_event",
        }
    ),
}


class ReactActionGovernance:
    """Intersect allowlists and enforce observation-backed business preconditions."""

    def __init__(self, specialists: dict[Any, Any]) -> None:
        self._specialists = specialists

    def validate(self, goal: Any, decision: Any, runtime: Any) -> str | None:
        if decision.capability not in self.effective_allowlist(goal, runtime):
            return "CAPABILITY_NOT_IN_SPECIALIST_ALLOWLIST"
        if not self._arguments_declared(goal, decision):
            return "CAPABILITY_ARGUMENT_NOT_DECLARED"
        if not self._arguments_valid(goal, decision):
            return "CAPABILITY_ARGUMENT_SCHEMA_INVALID"
        if not self._required_inputs_grounded(goal, decision):
            return "CAPABILITY_REQUIRED_INPUT_UNGROUNDED"
        if self._repeat_count(goal, decision) >= runtime.execution_policy.max_react_repeats:
            return "REACT_NO_PROGRESS"
        if decision.capability == "repair_create":
            repair_guard = self._repair_create_guard(goal, decision)
            if repair_guard:
                return repair_guard
        if goal.domain == "inspection" and self._inspection_preread_required(goal, decision):
            return "INSPECTION_PREREAD_REQUIRED"
        if decision.capability == "billing_consult" and not self._billing_rule_missing(goal):
            return "BILLING_RULE_NOT_PROVEN_MISSING"
        return None

    def effective_allowlist(self, goal: Any, runtime: Any) -> frozenset[str]:
        specialist_name = {
            "repair": SpecialistName.REPAIR,
            "billing": SpecialistName.BILLING,
            "announcement": SpecialistName.ANNOUNCEMENT,
            "inspection": SpecialistName.INSPECTION,
        }.get(goal.domain)
        specialist = self._specialists.get(specialist_name)
        registered = frozenset(getattr(specialist, "allowlist", ()))
        allowed = DOMAIN_ALLOWLISTS.get(goal.domain, frozenset()) & registered
        runtime_allowed = runtime.execution_policy.allowlist
        return allowed if runtime_allowed is None else allowed & runtime_allowed

    def _arguments_declared(self, goal: Any, decision: Any) -> bool:
        specialist_name = {
            "repair": SpecialistName.REPAIR,
            "billing": SpecialistName.BILLING,
            "announcement": SpecialistName.ANNOUNCEMENT,
            "inspection": SpecialistName.INSPECTION,
        }.get(goal.domain)
        specialist = self._specialists.get(specialist_name)
        inventory = tuple(getattr(specialist, "capability_inventory", ()))
        if not inventory:
            return True
        selected = next(
            (item for item in inventory if item.get("name") == decision.capability), None
        )
        if selected is None:
            return False
        declared = set(selected.get("required_inputs") or ()) | set(
            selected.get("optional_inputs") or ()
        )
        return set(decision.arguments) <= declared

    def _arguments_valid(self, goal: Any, decision: Any) -> bool:
        specialist_name = {
            "repair": SpecialistName.REPAIR,
            "billing": SpecialistName.BILLING,
            "announcement": SpecialistName.ANNOUNCEMENT,
            "inspection": SpecialistName.INSPECTION,
        }.get(goal.domain)
        specialist = self._specialists.get(specialist_name)
        validator = getattr(specialist, "arguments_valid", None)
        return (
            True if validator is None else bool(validator(decision.capability, decision.arguments))
        )

    def _required_inputs_grounded(self, goal: Any, decision: Any) -> bool:
        if not goal.authorized_domains:
            return True
        specialist_name = {
            "repair": SpecialistName.REPAIR,
            "billing": SpecialistName.BILLING,
            "announcement": SpecialistName.ANNOUNCEMENT,
            "inspection": SpecialistName.INSPECTION,
        }.get(goal.domain)
        specialist = self._specialists.get(specialist_name)
        inventory = tuple(getattr(specialist, "capability_inventory", ()))
        if not inventory:
            return True
        selected = next(
            (item for item in inventory if item.get("name") == decision.capability), None
        )
        required = set((selected or {}).get("required_inputs") or ())
        grounded_keys = set(goal.candidate_facts)
        grounded_values = _nested_value_hashes(goal.candidate_facts)
        for observation in goal.observations:
            if observation.ok:
                grounded_keys.update(_nested_keys(observation.data))
                grounded_values.update(_nested_value_hashes(observation.data))
        return all(
            name in grounded_keys or canonical_hash(decision.arguments.get(name)) in grounded_values
            for name in required
        )

    @staticmethod
    def _repair_create_guard(goal: Any, decision: Any) -> str | None:
        for item in goal.observations:
            if item.capability != "repair_list" or not item.ok:
                continue
            location = str(decision.arguments.get("location") or "").strip()
            category = str(decision.arguments.get("category") or "").strip()
            if location and str(item.data.get("query_location") or "") != location:
                continue
            if category and str(item.data.get("query_category") or "") != category:
                continue
            terminal = {"COMPLETED", "CANCELLED", "CLOSED", "REJECTED"}
            if any(
                str(value.get("status") or "").upper() not in terminal
                for value in item.data.get("items") or ()
            ):
                return "ACTIVE_REPAIR_EXISTS"
            return None
        return "REPAIR_PREREAD_REQUIRED"

    @staticmethod
    def _inspection_preread_required(goal: Any, decision: Any) -> bool:
        writes = {
            "inspection_start_task",
            "inspection_add_record",
            "inspection_submit_records",
            "security_event_submit_disposal",
            "close_high_risk_event",
        }
        if decision.capability not in writes or "lookup_existing_first" not in goal.constraints:
            return False
        reads = {"inspection_list", "inspection_get_task", "inspection_get_event"}
        return not any(item.ok and item.capability in reads for item in goal.observations)

    @staticmethod
    def _billing_rule_missing(goal: Any) -> bool:
        return any(
            item.capability == "billing_query" and item.ok and item.data.get("rule") is None
            for item in goal.observations
        )

    @staticmethod
    def _repeat_count(goal: Any, decision: Any) -> int:
        target = (decision.capability, canonical_hash(decision.arguments))
        return sum((item.capability, item.params_hash) == target for item in goal.observations)


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {nested for item in value.values() for nested in _nested_keys(item)}
    if isinstance(value, list | tuple):
        return {nested for item in value for nested in _nested_keys(item)}
    return set()


def _nested_value_hashes(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {canonical_hash(item) for item in value.values()} | {
            nested for item in value.values() for nested in _nested_value_hashes(item)
        }
    if isinstance(value, list | tuple):
        return {canonical_hash(item) for item in value} | {
            nested for item in value for nested in _nested_value_hashes(item)
        }
    return {canonical_hash(value)}
