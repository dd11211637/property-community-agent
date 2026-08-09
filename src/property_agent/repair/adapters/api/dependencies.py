"""
Repair API dependencies — service lookup and request-context adaptation.

The repair module keeps its own ``RequestContext`` (typed with the repair
``Role`` enum) so the domain never depends on platform string literals. This
adapter converts the authenticated platform context produced by the JWT
dependency into that domain-specific shape.
"""

from dataclasses import replace

from fastapi import Depends, Request

from property_agent.platform.adapters.api.dependencies import (
    RequestContext as PlatformRequestContext,
)
from property_agent.platform.adapters.api.dependencies import get_current_user
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.enums import Role
from property_agent.repair.domain.errors import BusinessError

# Platform role strings → repair domain roles. Roles the repair module does not
# model (SECURITY_GUARD, FINANCE, ...) are intentionally dropped rather than
# silently widened.
ROLE_MAP: dict[str, Role] = {
    "RESIDENT": Role.RESIDENT,
    "CUSTOMER_SERVICE": Role.CUSTOMER_SERVICE,
    "REPAIR_WORKER": Role.REPAIR_WORKER,
    "MANAGER": Role.MANAGER,
    "SYSTEM_ADMIN": Role.MANAGER,
}


def get_service(request: Request) -> WorkOrderService:
    service = getattr(request.app.state, "work_order_service", None)
    if not isinstance(service, WorkOrderService):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED",
            "The repair service has not been configured.",
            503,
        )
    return service


def to_repair_context(platform_context: PlatformRequestContext, request_id: str) -> RequestContext:
    """Project an authenticated platform context onto the repair domain."""
    roles = frozenset(ROLE_MAP[role] for role in platform_context.roles if role in ROLE_MAP)
    if not roles:
        raise BusinessError(
            "FORBIDDEN",
            "Your account has no role that can access the repair module.",
            403,
        )
    house_ids = frozenset(platform_context.bound_house_ids)
    if platform_context.current_house_id is not None:
        house_ids = house_ids | {platform_context.current_house_id}
    return RequestContext(
        actor_id=platform_context.actor_id,
        community_id=platform_context.community_id,
        roles=roles,
        request_id=request_id or platform_context.request_id,
        house_ids=house_ids,
    )


async def get_request_context(
    request: Request,
    platform_context: PlatformRequestContext = Depends(get_current_user),  # noqa: B008
) -> RequestContext:
    """Return the repair ``RequestContext`` for the current request.

    Precedence:
      1. A repair context already placed on ``request.state`` (used by the
         agent tool adapter, which runs outside the HTTP auth chain).
      2. The JWT-authenticated platform context, projected onto repair roles.
    """
    request_id = getattr(request.state, "request_id", "")

    preset = getattr(request.state, "request_context", None)
    if isinstance(preset, RequestContext):
        if request_id and preset.request_id != request_id:
            return replace(preset, request_id=request_id)
        return preset

    return to_repair_context(platform_context, request_id)
