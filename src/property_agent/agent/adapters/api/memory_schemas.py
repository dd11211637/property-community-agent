"""Validated user inputs for conversation history and long-term memory."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MemoryType = Literal["PREFERENCE", "COMMUNICATION", "ACCESSIBILITY", "SERVICE_NOTE"]


class CreateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryType
    content: str = Field(min_length=1, max_length=500)
    house_id: UUID | None = None
    source_conversation_id: str | None = Field(default=None, min_length=1, max_length=64)
    expires_at: datetime | None = None


class UpdateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)


class DeleteMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
