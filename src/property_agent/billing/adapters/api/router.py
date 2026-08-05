from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from property_agent.billing.adapters.api.dependencies import (
    get_billing_service,
    get_request_context,
)
from property_agent.billing.adapters.presentation import bill_data, explanation_data
from property_agent.billing.application.queries import BillSearch
from property_agent.billing.application.service import BillingService
from property_agent.billing.domain.enums import FeeType
from property_agent.platform.context import RequestContext
from property_agent.platform.responses import success_envelope as _success
from property_agent.platform.schemas import Envelope

router = APIRouter(prefix="/api/bills", tags=["billing"])
ServiceDependency = Annotated[BillingService, Depends(get_billing_service)]
ContextDependency = Annotated[RequestContext, Depends(get_request_context)]


@router.get("", response_model=Envelope)
def search_bills(
    service: ServiceDependency,
    context: ContextDependency,
    house_id: UUID | None = None,
    fee_type: Annotated[list[FeeType] | None, Query()] = None,
    period_from: date | None = None,
    period_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope:
    bills, total = service.search(
        BillSearch(
            house_id=house_id,
            fee_types=frozenset(fee_type or []),
            period_from=period_from,
            period_to=period_to,
            limit=limit,
            offset=offset,
        ),
        context,
    )
    return _success(
        {
            "items": [bill_data(bill) for bill in bills],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        context,
    )


@router.get("/{bill_id}", response_model=Envelope)
def get_bill(
    bill_id: UUID,
    service: ServiceDependency,
    context: ContextDependency,
) -> Envelope:
    return _success(bill_data(service.get(bill_id, context)), context)


@router.get("/{bill_id}/explanation", response_model=Envelope)
def explain_bill(
    bill_id: UUID,
    service: ServiceDependency,
    context: ContextDependency,
) -> Envelope:
    return _success(explanation_data(service.explain(bill_id, context)), context)
