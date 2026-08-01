from typing import Any

from property_agent.platform.context import RequestContext
from property_agent.platform.schemas import Envelope


def success_envelope(data: Any, context: RequestContext) -> Envelope:
    return Envelope(success=True, data=data, error=None, request_id=context.request_id)
