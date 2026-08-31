"""Canonical stateless Repair specialist."""

from property_agent.agent.orchestration import SpecialistName
from property_agent.agent.specialists.base import StatelessSpecialist


class RepairSpecialist(StatelessSpecialist):
    name = SpecialistName.REPAIR
    domain = "repair"

    def choose_capability(self, step, state, prior_results):
        del step, prior_results
        if state.slots.get("work_order_id"):
            return "repair_get"
        if state.slots.get("action") in {"create", "submit"}:
            return "repair_create"
        return "repair_list"

    def project_parameters(self, capability, step, state, prior_results):
        del prior_results
        values = {**state.slots, **step.parameters}
        if capability == "repair_get":
            return {"work_order_id": str(values.get("work_order_id") or "")}
        if capability == "repair_create":
            return {
                "description": str(values.get("description") or ""),
                "location": str(values.get("location") or ""),
                "urgency": str(values.get("urgency") or "NORMAL"),
            }
        return {
            "statuses": tuple(values.get("statuses") or ()),
            "limit": int(values.get("limit") or 20),
        }
