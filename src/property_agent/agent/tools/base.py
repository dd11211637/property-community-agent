"""工具层基座 — PRD §6.5.2 / §6.5.7。

约束（不可绕过）：

1. AI 层只允许调用各业务模块的**公开 Application Service**，禁止直接触碰
   仓储、ORM 或数据库会话。
2. 可信身份只能来自 API 层注入的 ``ContextProvider``；工具**不得**用
   ``GraphState`` 里的槽位伪造 actor / community / house。
3. 读工具直接执行；写-低风险工具必须已持有确认令牌且带确定性幂等键；
   写-高风险工具**永不执行**，只返回转人工接管指令。
4. 工具失败原样返回真实错误，不允许把模型自述当成业务成功证据。
"""

from collections.abc import Callable
from typing import Any, Protocol

from property_agent.agent.policies import OperationLevel, classify_operation_level
from property_agent.agent.state import GraphState
from property_agent.platform.application.hashing import canonical_hash

Tool = Callable[[GraphState], dict[str, Any]]


class ContextProvider(Protocol):
    """由 API 层注入：把可信请求上下文转换成目标模块的 RequestContext。"""

    def __call__(self, state: GraphState) -> Any:  # pragma: no cover - 协议声明
        ...


class ToolPreconditionError(RuntimeError):
    """工具前置条件不满足（缺确认令牌 / 缺必填参数 / 越权调用）。"""


def ok(tool: str, **data: Any) -> dict[str, Any]:
    return {"ok": True, "tool": tool, "data": data}


def handover(tool: str, reason: str, **detail: Any) -> dict[str, Any]:
    """高风险工具的唯一返回：不执行任何写操作，转授权人工。"""
    return {
        "ok": False,
        "tool": tool,
        "handover_required": True,
        "reason": reason,
        "detail": detail,
    }


def require_confirmation(state: GraphState, tool: str) -> str:
    """写-低风险工具的确认令牌门槛（PRD A-03：确认前不得落库）。"""
    token = (state.confirmation_token or "").strip()
    if not token:
        raise ToolPreconditionError(f"{tool} 需要用户确认后才能执行（缺少确认令牌）")
    return token


def require_slot(state: GraphState, name: str, tool: str) -> Any:
    value = state.slots.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolPreconditionError(f"{tool} 缺少必填参数：{name}")
    return value


def idempotency_key(state: GraphState, tool: str, params: Any) -> str:
    """确定性幂等键（PRD A-04）。

    同一会话 + 同一工具 + 同一参数 ⇒ 同一 key，重复确认只会命中重放而不会
    产生第二个业务对象。长度受各模块 128 字符上限约束。
    """
    digest = canonical_hash({"tool": tool, "params": params})
    return f"agent-{state.conversation_id}-{tool}-{digest[:32]}"[:128]


def assert_level(tool: str, expected: OperationLevel, intent: str | None = None) -> None:
    """自检：工具名在策略表中的等级必须与其实现方式一致。"""
    actual = classify_operation_level(intent or "", tool)
    if actual != expected.value:
        raise ToolPreconditionError(
            f"工具 {tool} 等级不一致：策略表={actual}，实现={expected.value}"
        )
