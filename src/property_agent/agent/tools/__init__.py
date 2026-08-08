"""智能体工具层 — PRD §6.5.2。

每个工具只是业务模块公开 Application Service 的薄封装：不绕过权限、不绕过
幂等、不绕过审计，也不复制业务规则。工具注册表由 API 层组装后注入执行节点。
"""

from property_agent.agent.tools.announcement import build_announcement_tools
from property_agent.agent.tools.base import (
    ContextProvider,
    Tool,
    ToolPreconditionError,
    handover,
    idempotency_key,
    ok,
)
from property_agent.agent.tools.billing import build_billing_tools
from property_agent.agent.tools.inspection import build_inspection_tools
from property_agent.agent.tools.repair import build_repair_tools

__all__ = [
    "ContextProvider",
    "Tool",
    "ToolPreconditionError",
    "build_announcement_tools",
    "build_billing_tools",
    "build_inspection_tools",
    "build_repair_tools",
    "handover",
    "idempotency_key",
    "ok",
]
