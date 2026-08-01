from fastapi import Request

from property_agent.platform.dependencies import get_request_context as get_request_context
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.errors import BusinessError


def get_service(request: Request) -> WorkOrderService:
    service = getattr(request.app.state, "work_order_service", None)
    if not isinstance(service, WorkOrderService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED",
            "The repair service has not been configured.",
            503,
        )
    return service
