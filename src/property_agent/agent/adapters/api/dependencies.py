"""智能体 API 依赖 — 身份接缝与运行时装配。

身份**只**来自平台认证层产出的可信 ``RequestContext``：
``actor_id`` / ``community_id`` / ``bound_house_ids`` / ``current_house_id``
全部由 JWT 解析得到，请求体里的任何自述身份一律忽略（PRD §6.5.2 / §6.5.4）。

运行时未装配时返回 503 ``ADAPTER_NOT_CONFIGURED``，与其它业务模块一致。
"""

from fastapi import Depends, Request

from property_agent.agent.application.facade import AgentRuntimeFacade
from property_agent.platform.context import RequestContext
from property_agent.platform.dependencies import get_request_context as get_request_context
from property_agent.platform.errors import BusinessError

AgentRequestContext = RequestContext


def get_agent_context(
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> AgentRequestContext:
    """Pass through the one authenticated platform context used by every domain."""
    return context


def get_agent_runner(request: Request) -> AgentRuntimeFacade:
    runner = getattr(request.app.state, "agent_runner", None)
    if not isinstance(runner, AgentRuntimeFacade):
        raise BusinessError(
            "ADAPTER_NOT_CONFIGURED", "The agent runtime has not been configured.", 503
        )
    return runner
