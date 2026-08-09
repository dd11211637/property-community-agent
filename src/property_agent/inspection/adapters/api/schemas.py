from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from property_agent.inspection.domain.enums import (
    EventRiskLevel,
    EventType,
    TaskRecordType,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedActionRequest(StrictModel):
    expected_version: int = Field(ge=1)


# ----------------------------- 巡检任务 -----------------------------
class CreateInspectionTaskRequest(StrictModel):
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    route_points: list[str] = Field(min_length=1)
    planned_at: datetime | None = None
    due_at: datetime | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)


class AssignTaskRequest(VersionedActionRequest):
    assignee_id: UUID


class SubmitTaskRecordsRequest(VersionedActionRequest):
    record_type: TaskRecordType
    point: str | None = None
    note: str = Field(min_length=1)
    confirmation_token: str | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)
    supplement_reason: str | None = None


class AddTaskRecordRequest(VersionedActionRequest):
    record_type: TaskRecordType
    point: str | None = None
    note: str = Field(min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list)
    is_supplement: bool = False
    actual_time: datetime | None = None
    supplement_reason: str | None = None


class AddAiSuggestionRequest(StrictModel):
    point: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    severity: str = "MEDIUM"
    model: str = "inspection-ai"


class ConfirmAiSuggestionsRequest(VersionedActionRequest):
    pass


# ----------------------------- 安防事件 -----------------------------
class CreateSecurityEventRequest(StrictModel):
    source_task_id: UUID | None = None
    event_type: EventType
    risk_level: EventRiskLevel
    location: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    confirmation_token: str | None = None
    report_source: str = "MANUAL"
    attachment_ids: list[UUID] = Field(default_factory=list)


class AssignEventRequest(VersionedActionRequest):
    assignee_id: UUID


class SubmitDisposalRequest(VersionedActionRequest):
    note: str = Field(min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list)


class ReturnEventRequest(VersionedActionRequest):
    note: str = Field(min_length=1)


# ----------------------------- 统一信封 -----------------------------
class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class Envelope(BaseModel):
    success: bool
    data: Any = None
    error: ErrorBody | None = None
    request_id: str
