from fastapi import Request

from property_agent.announcement.application.service import AnnouncementService
from property_agent.platform.dependencies import get_request_context as get_request_context
from property_agent.platform.errors import BusinessError


def get_announcement_service(request: Request) -> AnnouncementService:
    service = getattr(request.app.state, "announcement_service", None)
    if not isinstance(service, AnnouncementService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED", "The announcement service has not been configured.", 503
        )
    return service
