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
            parameters = {
                "description": str(values.get("description") or ""),
                "location": str(values.get("location") or ""),
                "urgency": str(values.get("urgency") or "NORMAL"),
            }
            if values.get("category"):
                parameters["category"] = str(values["category"])
            for key in ("contact_name", "contact_phone", "access_instructions"):
                if values.get(key):
                    parameters[key] = str(values[key])
            if values.get("preferred_time_windows"):
                parameters["preferred_time_windows"] = tuple(values["preferred_time_windows"])
            return parameters
        return {
            "statuses": tuple(values.get("statuses") or ()),
            "limit": int(values.get("limit") or 20),
        }

    def success_message(self, capability, data):
        if capability == "repair_list":
            return f"共查到 {data.get('count', 0)} 条报修工单。"
        if capability == "repair_get":
            return "已查到该报修工单的详情和进度。"
        return "报修工单已创建。"
