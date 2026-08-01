from fastapi import Request

from property_agent.billing.application.service import BillingService
from property_agent.platform.dependencies import get_request_context as get_request_context
from property_agent.platform.errors import BusinessError


def get_billing_service(request: Request) -> BillingService:
    service = getattr(request.app.state, "billing_service", None)
    if not isinstance(service, BillingService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED",
            "The billing service has not been configured.",
            503,
        )
    return service
