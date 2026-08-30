"""Stable repair presentation contracts used by OpenAPI and Frontend V2."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from property_agent.platform.schemas import Envelope


class PresentationModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AppointmentResponse(PresentationModel):
    appointment_at: str
    note: str
    recorded_at: str


class WorkOrderResponse(PresentationModel):
    id: UUID
    business_no: str
    community_id: UUID
    house_id: UUID
    house_display: str | None = None
    reporter_id: UUID
    reporter_name: str | None = None
    category: str
    location: str
    description: str
    urgency: str
    status: str
    assignee_id: UUID | None
    assignee_name: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    access_instructions: str | None = None
    preferred_time_windows: list[str] = Field(default_factory=list)
    request_attachment_ids: list[UUID] = Field(default_factory=list)
    completion_attachment_ids: list[UUID] = Field(default_factory=list)
    current_appointment: AppointmentResponse | None = None
    service_phase: str = "REQUESTED"
    version: int
    available_actions: list[str]
    has_review: bool
    created_at: str
    updated_at: str
    closed_at: str | None


class WorkOrderListResponse(PresentationModel):
    items: list[WorkOrderResponse]
    limit: int
    offset: int


class WorkOrderTimelineEntryResponse(PresentationModel):
    entry_type: str
    action: str
    operator_id: UUID
    created_at: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    note: str | None
    attachment_ids: list[UUID]
    appointment_at: str | None = None


WorkOrderEnvelope = Envelope[WorkOrderResponse]
WorkOrderListEnvelope = Envelope[WorkOrderListResponse]
WorkOrderTimelineEnvelope = Envelope[list[WorkOrderTimelineEntryResponse]]
