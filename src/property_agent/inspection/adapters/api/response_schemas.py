"""Stable inspection and security presentation contracts."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from property_agent.platform.schemas import Envelope


class PresentationModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class InspectionTaskResponse(PresentationModel):
    id: UUID
    business_no: str
    community_id: UUID
    title: str
    description: str
    route_points: list[str]
    status: str
    assignee_id: UUID | None
    planned_at: str | None
    due_at: str | None
    version: int
    available_actions: list[str]
    ai_suggestions: list[dict[str, Any]]
    ai_pending_confirm: bool
    created_at: str
    updated_at: str
    closed_at: str | None


class SecurityEventResponse(PresentationModel):
    id: UUID
    business_no: str
    community_id: UUID
    source_task_id: UUID | None
    reporter_id: UUID
    event_type: str
    risk_level: str
    location: str
    description: str
    status: str
    assignee_id: UUID | None
    grade_confirmed_by: UUID | None
    version: int
    available_actions: list[str]
    created_at: str
    updated_at: str
    closed_at: str | None


class ResourceListResponse(BaseModel):
    items: list[Any]
    limit: int
    offset: int


class TimelineEntryResponse(PresentationModel):
    entry_type: str
    action: str
    operator_id: UUID
    created_at: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    note: str | None
    attachment_ids: list[UUID]


class InspectionTaskListResponse(ResourceListResponse):
    items: list[InspectionTaskResponse]


class SecurityEventListResponse(ResourceListResponse):
    items: list[SecurityEventResponse]


InspectionTaskEnvelope = Envelope[InspectionTaskResponse]
InspectionTaskListEnvelope = Envelope[InspectionTaskListResponse]
SecurityEventEnvelope = Envelope[SecurityEventResponse]
SecurityEventListEnvelope = Envelope[SecurityEventListResponse]
TimelineEnvelope = Envelope[list[TimelineEntryResponse]]
