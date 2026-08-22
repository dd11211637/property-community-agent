"""智能体会话错误 — PRD §6.5.8 / §6.5.10。

恢复失败必须给出**明确的失败原因**，不能沉默地继续执行待确认的写操作。
每个错误码都带稳定的 HTTP 状态，供 API 层直接渲染统一错误信封。
"""

from enum import StrEnum


class AgentSessionErrorCode(StrEnum):
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    CONVERSATION_CLOSED = "CONVERSATION_CLOSED"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    CHECKPOINT_NOT_FOUND = "CHECKPOINT_NOT_FOUND"
    NOTHING_PENDING = "NOTHING_PENDING"
    HOUSE_BINDING_REVOKED = "HOUSE_BINDING_REVOKED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_PARAMS_CHANGED = "CONFIRMATION_PARAMS_CHANGED"
    CONVERSATION_BUSY = "CONVERSATION_BUSY"


_DEFAULT_MESSAGES = {
    AgentSessionErrorCode.CONVERSATION_NOT_FOUND: "会话不存在。",
    AgentSessionErrorCode.CONVERSATION_CLOSED: "会话已结束，请重新发起。",
    AgentSessionErrorCode.SESSION_MISMATCH: "会话归属校验未通过，无法继续该会话。",
    AgentSessionErrorCode.CHECKPOINT_NOT_FOUND: "没有找到可恢复的会话状态。",
    AgentSessionErrorCode.NOTHING_PENDING: "当前没有待确认的操作。",
    AgentSessionErrorCode.HOUSE_BINDING_REVOKED: "房屋绑定已变更，请重新选择房屋后再试。",
    AgentSessionErrorCode.CONFIRMATION_EXPIRED: "确认已超时失效，请重新发起并确认。",
    AgentSessionErrorCode.CONFIRMATION_PARAMS_CHANGED: "操作内容已变化，请重新确认。",
    AgentSessionErrorCode.CONVERSATION_BUSY: "该会话正在被另一个请求处理，请稍后重试。",
}

_STATUS_CODES = {
    AgentSessionErrorCode.CONVERSATION_NOT_FOUND: 404,
    AgentSessionErrorCode.CHECKPOINT_NOT_FOUND: 404,
    AgentSessionErrorCode.SESSION_MISMATCH: 403,
    AgentSessionErrorCode.CONVERSATION_CLOSED: 409,
    AgentSessionErrorCode.NOTHING_PENDING: 409,
    AgentSessionErrorCode.HOUSE_BINDING_REVOKED: 409,
    AgentSessionErrorCode.CONFIRMATION_EXPIRED: 409,
    AgentSessionErrorCode.CONFIRMATION_PARAMS_CHANGED: 409,
    AgentSessionErrorCode.CONVERSATION_BUSY: 409,
}


class AgentSessionError(RuntimeError):
    """会话所有权 / 生命周期 / 恢复校验失败。"""

    def __init__(self, code: AgentSessionErrorCode, message: str | None = None) -> None:
        self.code = code.value
        self.message = message or _DEFAULT_MESSAGES[code]
        self.status_code = _STATUS_CODES[code]
        super().__init__(self.message)
