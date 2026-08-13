from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from property_agent.announcement.domain.enums import AnnouncementCategory


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateAnnouncementRequest(StrictModel):
    title: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1)
    category: AnnouncementCategory
    audience_condition: dict[str, list[str]] = Field(default_factory=dict)
    scheduled_at: datetime | None = None


class EditAnnouncementRequest(CreateAnnouncementRequest):
    expected_version: int = Field(ge=1)


class VersionedActionRequest(StrictModel):
    expected_version: int = Field(ge=1)


class RejectAnnouncementRequest(VersionedActionRequest):
    reason: str = Field(min_length=1)


class PublishAnnouncementRequest(VersionedActionRequest):
    confirmation_token: str = Field(min_length=1)


class ScheduleAnnouncementRequest(PublishAnnouncementRequest):
    scheduled_at: datetime


class WithdrawAnnouncementRequest(VersionedActionRequest):
    reason: str = Field(min_length=1)
