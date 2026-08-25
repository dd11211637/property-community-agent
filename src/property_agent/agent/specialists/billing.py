"""Stateless Billing specialist."""

from property_agent.agent.orchestration import SpecialistName
from property_agent.agent.specialists.base import StatelessSpecialist


class BillingSpecialist(StatelessSpecialist):
    name = SpecialistName.BILLING
    domain = "billing"

    def choose_capability(self, step, state, prior_results):
        del step, prior_results
        return "billing_consult" if state.slots.get("action") == "consult" else "billing_query"

    def project_parameters(self, capability, step, state, prior_results):
        del prior_results
        values = {**state.slots, **step.parameters}
        if capability == "billing_consult":
            return {
                "subject": str(values.get("subject") or ""),
                "description": str(values.get("description") or ""),
                "bill_id": values.get("bill_id"),
            }
        return {
            "query_type": str(values.get("query_type") or "list"),
            "period": values.get("period"),
            "fee_type": values.get("fee_type"),
            "bill_id": values.get("bill_id"),
        }

    def success_message(self, capability, data):
        if capability == "billing_query":
            return f"已查询账单，找到 {data.get('count') or 0} 条记录。"
        return "账单咨询已提交。"
