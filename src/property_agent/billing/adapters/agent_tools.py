from datetime import date
from typing import Any
from uuid import UUID

from property_agent.billing.adapters.presentation import bill_data, explanation_data
from property_agent.billing.application.queries import BillSearch
from property_agent.billing.application.service import BillingService
from property_agent.billing.domain.enums import FeeType
from property_agent.platform.context import RequestContext


class BillingToolAdapter:
    """Framework-neutral, read-only billing tools for future Agent orchestration."""

    def __init__(self, service: BillingService) -> None:
        self._service = service

    def search_bills(self, payload: dict[str, Any], context: RequestContext) -> dict[str, Any]:
        fee_types = frozenset(FeeType(value) for value in payload.get("fee_types", []))
        house_id = UUID(payload["house_id"]) if payload.get("house_id") else None
        bills, total = self._service.search(
            BillSearch(
                house_id=house_id,
                fee_types=fee_types,
                period_from=(
                    date.fromisoformat(payload["period_from"])
                    if payload.get("period_from")
                    else None
                ),
                period_to=(
                    date.fromisoformat(payload["period_to"]) if payload.get("period_to") else None
                ),
                limit=int(payload.get("limit", 50)),
                offset=int(payload.get("offset", 0)),
            ),
            context,
        )
        return {"items": [bill_data(bill) for bill in bills], "total": total}

    def get_bill(self, bill_id: str, context: RequestContext) -> dict[str, Any]:
        return bill_data(self._service.get(UUID(bill_id), context))

    def explain_bill(self, bill_id: str, context: RequestContext) -> dict[str, Any]:
        return explanation_data(self._service.explain(UUID(bill_id), context))
