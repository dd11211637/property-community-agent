from fastapi import Request

from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.errors import BusinessError
from property_agent.platform.dependencies import get_request_context as get_request_context


def get_task_service(request: Request) -> InspectionTaskService:
    service = getattr(request.app.state, "task_service", None)
    if not isinstance(service, InspectionTaskService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED", "The inspection task service has not been configured.", 503
        )
    return service


def get_event_service(request: Request) -> SecurityEventService:
    service = getattr(request.app.state, "event_service", None)
    if not isinstance(service, SecurityEventService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED", "The security event service has not been configured.", 503
        )
    return service
