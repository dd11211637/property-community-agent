from dataclasses import replace

from fastapi import Request

from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError


def get_request_context(request: Request) -> RequestContext:
    """Return identity data injected by trusted authentication middleware."""

    context = getattr(request.state, "request_context", None)
    if not isinstance(context, RequestContext):
        raise BusinessError("AUTH_REQUIRED", "Authentication is required.", 401)
    request_id = getattr(request.state, "request_id", "")
    if request_id and context.request_id != request_id:
        return replace(context, request_id=request_id)
    return context
