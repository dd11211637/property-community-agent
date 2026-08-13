"""
Platform API schemas — Pydantic models for request/response validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码")


class LoginResponse(BaseModel):
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    actor_id: UUID = Field(..., description="用户ID")
    display_name: str = Field(..., description="显示名称")
    community_id: UUID = Field(..., description="当前社区ID")
    community_name: str = Field(..., description="当前社区名称")
    roles: list[str] = Field(..., description="角色列表")
    house_ids: list[UUID] = Field(..., description="绑定的房屋ID列表")
    current_house_id: UUID | None = Field(None, description="当前房屋ID(单房屋自动选择)")


class HouseSelectionRequest(BaseModel):
    house_id: UUID = Field(..., description="要切换到的房屋ID")


class HouseSelectionResponse(BaseModel):
    house_id: UUID = Field(..., description="当前房屋ID")
    building: str = Field(..., description="楼栋")
    unit: str = Field(..., description="单元")
    room_no: str = Field(..., description="房号")


class StaffOptionResponse(BaseModel):
    id: UUID
    display_name: str
    role: str


# ═══════════════════════════════════════════════════════════════
# Generic Envelope (matches repair/inspection pattern)
# ═══════════════════════════════════════════════════════════════


class Envelope(BaseModel):
    success: bool = True
    data: Any = None
    error: dict[str, Any] | None = None
    request_id: str = ""


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


# ═══════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════


class HealthResponse(BaseModel):
    status: str = "ok"
    timestamp: datetime


class ReadyResponse(BaseModel):
    status: str  # "ready" or "not_ready"
    database: str  # "connected" or "disconnected"
    services: dict[str, str]  # service_name -> "configured" / "not_configured"
    timestamp: datetime


# ═══════════════════════════════════════════════════════════════
# Idempotency
# ═══════════════════════════════════════════════════════════════


class IdempotencyCheckResponse(BaseModel):
    is_replay: bool
    cached_response: dict[str, Any] | None = None


# ═══════════════════════════════════════════════════════════════
# Confirmation
# ═══════════════════════════════════════════════════════════════


class ConfirmationGenerateRequest(BaseModel):
    action: str = Field(..., description="操作类型")
    parameters: dict[str, Any] = Field(..., description="操作参数")


class ConfirmationGenerateResponse(BaseModel):
    token: str = Field(..., description="确认令牌")
    expires_in_seconds: int = Field(300, description="有效期(秒)")
