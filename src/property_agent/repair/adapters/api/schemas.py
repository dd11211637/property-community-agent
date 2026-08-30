from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from property_agent.repair.domain.enums import (
    ProcessRecordType,
    RepairCategory,
    Urgency,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateWorkOrderRequest(StrictModel):
    house_id: UUID
    category: RepairCategory
    location: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    urgency: Urgency
    confirmation_token: str = Field(min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list)
    contact_name: str | None = Field(default=None, max_length=128)
    contact_phone: str | None = Field(default=None, max_length=32)
    access_instructions: str | None = Field(default=None, max_length=1000)
    preferred_time_windows: list[str] = Field(default_factory=list, max_length=5)


class VersionedActionRequest(StrictModel):
    expected_version: int = Field(ge=1)


class AssignRequest(VersionedActionRequest):
    assignee_id: UUID


class RejectRequest(VersionedActionRequest):
    reason: str = Field(min_length=1)


class ProgressRequest(VersionedActionRequest):
    record_type: ProcessRecordType
    note: str = Field(min_length=1)
    appointment_at: datetime | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)


class CompletionRequest(VersionedActionRequest):
    note: str = Field(min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list)


class ReworkRequest(VersionedActionRequest):
    reason: str = Field(min_length=1)


class ReviewRequest(StrictModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class Envelope(BaseModel):
    success: bool
    data: Any = None
    error: ErrorBody | None = None
    request_id: str
