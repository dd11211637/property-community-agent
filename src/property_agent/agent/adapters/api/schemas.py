"""智能体 API 请求体 — PRD §6.5.2。

请求体里**没有** actor / community / role 字段：身份只从可信上下文取。
``house_id`` 允许显式指定当前房屋，但服务端会校验它确实在绑定列表内。
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="用户输入")
    house_id: UUID | None = Field(default=None, description="本轮针对的房屋（需已绑定）")
    slots: dict[str, Any] | None = Field(
        default=None, description="前端表单补齐的结构化槽位（可选）"
    )


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = Field(description="用户是否确认执行")
    action_hash: str | None = Field(
        default=None, max_length=128, description="确认卡片回带的参数指纹"
    )

    @model_validator(mode="after")
    def require_action_hash_for_confirmation(self) -> "ConfirmRequest":
        if self.confirmed and not self.action_hash:
            raise ValueError("确认操作必须回带确认卡片的参数指纹")
        return self
