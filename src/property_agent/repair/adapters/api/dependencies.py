from dataclasses import replace

from fastapi import Request

from property_agent.repair.application.ports import RequestContext
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


def get_request_context(request: Request) -> RequestContext:
    context = getattr(request.state, "request_context", None)
    if not isinstance(context, RequestContext):
        raise BusinessError("AUTH_REQUIRED", "Authentication is required.", 401)
    request_id = getattr(request.state, "request_id", "")
    if request_id and context.request_id != request_id:
        return replace(context, request_id=request_id)
    return context
