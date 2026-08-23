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
    confirmation_token: str | None = None
    approval_ref: str | None = None


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
    # P0 审批原子化：服务端确认时生成的 PENDING 审批引用，业务 UoW
    # 内消费（CONSUMED）与 publish mutation / 审计 / Outbox 同事务提交。
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleAnnouncementCommand:
    expected_version: int
    scheduled_at: datetime
    confirmation_token: str
    # P0 审批原子化：见 ``ReviewActionCommand.approval_ref`` 注释。
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AnnouncementSearch:
    statuses: tuple[str, ...] = ()
    limit: int = 50
    offset: int = 0
