from dataclasses import dataclass
from datetime import datetime
from typing import Any

from property_agent.announcement.domain.enums import (
    AnnouncementAction,
    AnnouncementCategory,
    VersionSource,
)


@dataclass(frozen=True, slots=True)
class CreateAnnouncementCommand:
    title: str
    body: str
    category: AnnouncementCategory
    audience_condition: dict[str, Any]
    scheduled_at: datetime | None = None
    source: VersionSource = VersionSource.MANUAL


@dataclass(frozen=True, slots=True)
class EditAnnouncementCommand:
    title: str
    body: str
    category: AnnouncementCategory
    audience_condition: dict[str, Any]
    expected_version: int
    source: VersionSource = VersionSource.MANUAL


@dataclass(frozen=True, slots=True)
class ReviewActionCommand:
    action: AnnouncementAction
    expected_version: int
    reason: str | None = None
    confirmation_token: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleAnnouncementCommand:
    expected_version: int
    scheduled_at: datetime
    confirmation_token: str


@dataclass(frozen=True, slots=True)
class AnnouncementSearch:
    statuses: tuple[str, ...] = ()
    limit: int = 50
    offset: int = 0
