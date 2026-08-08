"""智能体 API 请求体 — PRD §6.5.2。

请求体里**没有** actor / community / role 字段：身份只从可信上下文取。
``house_id`` 允许显式指定当前房屋，但服务端会校验它确实在绑定列表内。
"""

from uuid import UUID

from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="用户输入")
    house_id: UUID | None = Field(default=None, description="本轮针对的房屋（需已绑定）")
    slots: dict[str, str] | None = Field(
        default=None, description="前端表单补齐的结构化槽位（可选）"
    )


class ConfirmRequest(BaseModel):
    confirmed: bool = Field(description="用户是否确认执行")
    confirmation_token: str | None = Field(
        default=None, max_length=256, description="平台下发的确认令牌"
    )
    action_hash: str | None = Field(
        default=None, max_length=128, description="确认卡片回带的参数指纹"
    )
