"""Stable announcement presentation contracts used by OpenAPI and Frontend V2."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from property_agent.platform.schemas import Envelope


class PresentationModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AnnouncementResponse(PresentationModel):
    id: UUID
    business_no: str
    community_id: UUID
    title: str
    body: str
    category: str
    audience_condition: dict[str, list[str]]
    scheduled_at: str | None
    status: str
    version: int
    manager_recheck_required: bool
    published_at: str | None
    withdrawn_at: str | None
    available_actions: list[str]
    created_at: str
    updated_at: str


class AnnouncementListResponse(PresentationModel):
    items: list[AnnouncementResponse]
    limit: int
    offset: int


class AudienceSnapshotResponse(PresentationModel):
    condition: dict[str, list[str]]
    count: int
    samples: list[Any]
    generated_at: str


class AnnouncementVersionResponse(PresentationModel):
    version_no: int
    title: str
    body: str
    category: str
    audience_condition: dict[str, list[str]]
    operator_id: UUID
    source: str
    created_at: str


AnnouncementEnvelope = Envelope[AnnouncementResponse]
AnnouncementListEnvelope = Envelope[AnnouncementListResponse]
AudienceSnapshotEnvelope = Envelope[AudienceSnapshotResponse]
AnnouncementVersionsEnvelope = Envelope[list[AnnouncementVersionResponse]]
