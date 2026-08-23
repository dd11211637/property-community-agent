from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Request

from property_agent.inspection.adapters.api.state import InspectionAppState
from property_agent.inspection.application.ports import RequestContext
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.enums import Role
from property_agent.inspection.domain.errors import BusinessError
from property_agent.platform.adapters.api.dependencies import (
    RequestContext as PlatformRequestContext,
)
from property_agent.platform.adapters.api.dependencies import get_current_user

# 平台数据库角色字符串 → 巡检领域 Role 枚举。巡检不建模 REPAIR_WORKER / FINANCE，
# 这些角色不会给巡检模块带来任何能力；SYSTEM_ADMIN 视为管理者。
ROLE_MAP: dict[str, Role] = {
    "RESIDENT": Role.RESIDENT,
    "CUSTOMER_SERVICE": Role.CUSTOMER_SERVICE,
    "SECURITY_GUARD": Role.SECURITY_STAFF,
    "MANAGER": Role.MANAGER,
    "SYSTEM_ADMIN": Role.MANAGER,
}


def to_inspection_context(
    platform_context: PlatformRequestContext, request_id: str
) -> RequestContext:
    """把经 JWT 认证的平台上下文投影到巡检领域上下文。"""
    roles = frozenset(ROLE_MAP[r] for r in platform_context.roles if r in ROLE_MAP)
    if not roles:
        raise BusinessError(
            "FORBIDDEN",
            "Your account has no role that can access the inspection module.",
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
        execution_source=platform_context.execution_source,
        agent_lease=platform_context.agent_lease,
    )


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


async def get_request_context(
    request: Request,
    platform_context: Annotated[PlatformRequestContext, Depends(get_current_user)],
) -> RequestContext:
    """返回巡检领域 RequestContext。

    优先级：
      1. 已在 ``request.state`` 上预设的巡检上下文（智能体工具或测试使用）；
      2. JWT 认证得到的平台上下文，投影到巡检角色。
    """
    request_id = getattr(request.state, "request_id", "")
    preset = getattr(request.state, "request_context", None)
    if isinstance(preset, RequestContext):
        if request_id and preset.request_id != request_id:
            return replace(preset, request_id=request_id)
        return preset
    return to_inspection_context(platform_context, request_id)
