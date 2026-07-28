from dataclasses import replace

from fastapi import Request

from property_agent.inspection.adapters.api.state import (
    InspectionAppState,
)
from property_agent.inspection.application.ports import RequestContext
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.errors import BusinessError


def get_task_service(request: Request) -> InspectionTaskService:
    state: InspectionAppState = request.app.state  # type: ignore[attr-defined]
    service = getattr(state, "task_service", None)
    if not isinstance(service, InspectionTaskService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED", "The inspection task service has not been configured.", 503
        )
    return service


def get_event_service(request: Request) -> SecurityEventService:
    state: InspectionAppState = request.app.state  # type: ignore[attr-defined]
    service = getattr(state, "event_service", None)
    if not isinstance(service, SecurityEventService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED", "The security event service has not been configured.", 503
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
