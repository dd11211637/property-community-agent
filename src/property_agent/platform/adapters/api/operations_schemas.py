"""Typed message-center and read-only admin presentation contracts."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from property_agent.platform.schemas import Envelope


class PresentationModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MessageResponse(PresentationModel):
    id: UUID
    business_type: str
    resource_id: str
    title: str
    body: str
    status: str
    is_read: bool
    read_at: str | None
    retry_count: int
    max_retry_count: int
    retry_exhausted: bool
    last_error: str | None
    handover_status: str | None
    fallback_contact: str | None
    created_at: str
    updated_at: str


class MessageListResponse(PresentationModel):
    items: list[MessageResponse]
    total: int
    limit: int
    offset: int


class MarkAllReadResponse(PresentationModel):
    updated_count: int
    read_at: str


class PendingHandoverResponse(PresentationModel):
    id: UUID
    source: str
    queue: str
    summary: str
    status: str
    created_at: str


class HighRiskEventResponse(PresentationModel):
    id: UUID
    business_no: str
    location: str
    risk_level: str
    status: str
    updated_at: str


class IntegrationHealthResponse(PresentationModel):
    database: str
    message_delivery: str
    model_gateway: str


class AdminDashboardResponse(PresentationModel):
    pending_count: int
    failed_message_count: int
    high_risk_event_count: int
    pending_items: list[PendingHandoverResponse]
    failed_messages: list[MessageResponse]
    high_risk_events: list[HighRiskEventResponse]
    integration_health: IntegrationHealthResponse


MessageEnvelope = Envelope[MessageResponse]
MessageListEnvelope = Envelope[MessageListResponse]
MarkAllReadEnvelope = Envelope[MarkAllReadResponse]
AdminDashboardEnvelope = Envelope[AdminDashboardResponse]
