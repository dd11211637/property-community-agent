from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from property_agent.announcement.domain.enums import (
    AnnouncementAction,
    AnnouncementStatus,
    VersionSource,
)
from property_agent.announcement.domain.errors import invalid_transition

TRANSITIONS: dict[tuple[AnnouncementStatus, AnnouncementAction], AnnouncementStatus] = {
    (AnnouncementStatus.DRAFT, AnnouncementAction.SUBMIT_REVIEW): AnnouncementStatus.PENDING_REVIEW,
    (
        AnnouncementStatus.REJECTED,
        AnnouncementAction.SUBMIT_REVIEW,
    ): AnnouncementStatus.PENDING_REVIEW,
    (AnnouncementStatus.PENDING_REVIEW, AnnouncementAction.APPROVE): AnnouncementStatus.APPROVED,
    (AnnouncementStatus.PENDING_REVIEW, AnnouncementAction.REJECT): AnnouncementStatus.REJECTED,
    (AnnouncementStatus.APPROVED, AnnouncementAction.PUBLISH): AnnouncementStatus.PUBLISHED,
    (AnnouncementStatus.PUBLISHED, AnnouncementAction.WITHDRAW): AnnouncementStatus.WITHDRAWN,
    (AnnouncementStatus.PUBLISHED, AnnouncementAction.ARCHIVE): AnnouncementStatus.ARCHIVED,
}


@dataclass(frozen=True, slots=True)
class AnnouncementVersion:
    version_no: int
    title: str
    body: str
    category: str
    audience_condition: dict[str, list[str]]
    operator_id: UUID
    source: VersionSource
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AudienceSnapshot:
    condition: dict[str, list[str]]
    member_ids: tuple[UUID, ...]
    count: int
    samples: tuple[dict[str, str], ...]
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class AnnouncementReview:
    action: AnnouncementAction
    reviewer_id: UUID
    reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AnnouncementWithdrawal:
    withdrawn_by: UUID
    reason: str
    version_no: int
    created_at: datetime


@dataclass(slots=True)
class Announcement:
    id: UUID
    community_id: UUID
    business_no: str
    title: str
    body: str
    category: str
    audience_condition: dict[str, list[str]]
    created_by: UUID
    create_idempotency_key: str
    scheduled_at: datetime | None = None
    status: AnnouncementStatus = AnnouncementStatus.DRAFT
    version: int = 1
    manager_recheck_required: bool = False
    published_at: datetime | None = None
    withdrawn_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def transition(self, action: AnnouncementAction, *, now: datetime | None = None) -> None:
        target = TRANSITIONS.get((self.status, action))
        if target is None:
            raise invalid_transition(
                self.status.value, action.value, [item.value for item in self.state_actions()]
            )
        self.status = target
        self.version += 1
        self.updated_at = now or datetime.now(UTC)
        if action == AnnouncementAction.PUBLISH:
            self.published_at = self.updated_at
        if action == AnnouncementAction.WITHDRAW:
            self.withdrawn_at = self.updated_at

    def edit(
        self,
        *,
        title: str,
        body: str,
        category: str,
        audience_condition: dict[str, list[str]],
        now: datetime,
    ) -> None:
        if self.status not in {AnnouncementStatus.DRAFT, AnnouncementStatus.REJECTED}:
            raise invalid_transition(self.status.value, AnnouncementAction.EDIT.value, [])
        self.title = title
        self.body = body
        self.category = category
        self.audience_condition = audience_condition
        self.version += 1
        self.updated_at = now

    def state_actions(self) -> tuple[AnnouncementAction, ...]:
        return tuple(action for (status, action), _ in TRANSITIONS.items() if status == self.status)
