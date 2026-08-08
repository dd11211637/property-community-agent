from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from property_agent.announcement.application.commands import AnnouncementSearch
from property_agent.announcement.application.ports import IdempotencyRecord
from property_agent.announcement.domain.entities import (
    Announcement,
    AnnouncementReview,
    AnnouncementVersion,
    AudienceSnapshot,
)


@dataclass
class FakeState:
    announcements: dict[UUID, Announcement] = field(default_factory=dict)
    versions: dict[UUID, list[AnnouncementVersion]] = field(default_factory=dict)
    reviews: list[AnnouncementReview] = field(default_factory=list)
    snapshots: dict[UUID, list[AudienceSnapshot]] = field(default_factory=dict)
    idempotency: dict[tuple[UUID, str, str], IdempotencyRecord] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)


class FakeRepository:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def add(self, announcement: Announcement) -> None:
        self.state.announcements[announcement.id] = announcement

    def save(self, announcement: Announcement) -> None:
        self.state.announcements[announcement.id] = announcement

    def get(self, announcement_id: UUID, community_id: UUID) -> Announcement | None:
        item = self.state.announcements.get(announcement_id)
        return item if item and item.community_id == community_id else None

    def list(self, community_id: UUID, search: AnnouncementSearch) -> list[Announcement]:
        items = [
            item for item in self.state.announcements.values() if item.community_id == community_id
        ]
        if search.statuses:
            items = [item for item in items if item.status.value in search.statuses]
        return items[search.offset : search.offset + search.limit]

    def add_version(
        self, announcement_id: UUID, community_id: UUID, version: AnnouncementVersion
    ) -> None:
        self.state.versions.setdefault(announcement_id, []).append(version)

    def versions(self, announcement_id: UUID, community_id: UUID) -> list[AnnouncementVersion]:
        return list(self.state.versions.get(announcement_id, ()))

    def add_review(
        self, announcement_id: UUID, community_id: UUID, review: AnnouncementReview
    ) -> None:
        self.state.reviews.append(review)

    def add_audience_snapshot(
        self, announcement_id: UUID, community_id: UUID, snapshot: AudienceSnapshot
    ) -> None:
        self.state.snapshots.setdefault(announcement_id, []).append(snapshot)

    def latest_audience_snapshot(
        self, announcement_id: UUID, community_id: UUID
    ) -> AudienceSnapshot | None:
        snapshots = self.state.snapshots.get(announcement_id, [])
        return snapshots[-1] if snapshots else None


class FakeIdempotency:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def get(self, actor_id: UUID, operation: str, key: str) -> IdempotencyRecord | None:
        return self.state.idempotency.get((actor_id, operation, key))

    def add(self, record: IdempotencyRecord) -> None:
        self.state.idempotency[(record.actor_id, record.operation, record.key)] = record


class FakeAudit:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def add(self, **event: Any) -> None:
        self.state.audits.append(event)


class FakeAudienceResolver:
    def __init__(self, member_ids: tuple[UUID, ...] = ()) -> None:
        self.member_ids = member_ids

    def resolve(self, *, community_id, condition, request_id) -> AudienceSnapshot:
        return AudienceSnapshot(
            condition=condition,
            member_ids=self.member_ids,
            count=len(self.member_ids),
            samples=tuple({"member": "masked"} for _ in self.member_ids[:3]),
            generated_at=datetime.now(UTC),
        )


class FakeUnitOfWork:
    def __init__(self, state: FakeState, audiences: FakeAudienceResolver) -> None:
        self.announcements = FakeRepository(state)
        self.idempotency = FakeIdempotency(state)
        self.audit = FakeAudit(state)
        self.audiences = audiences
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type:
            self.rollback()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.committed = False


class Harness:
    def __init__(self, *, audience_members: tuple[UUID, ...] = ()) -> None:
        self.state = FakeState()
        self.audiences = FakeAudienceResolver(audience_members)

    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.state, self.audiences)
