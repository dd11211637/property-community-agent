from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from property_agent.announcement.application.commands import AnnouncementSearch
from property_agent.announcement.domain.entities import (
    Announcement,
    AnnouncementReview,
    AnnouncementVersion,
    AnnouncementWithdrawal,
    AudienceSnapshot,
)
from property_agent.platform.context import RequestContext


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    actor_id: UUID
    operation: str
    key: str
    request_hash: str
    resource_id: UUID
    response_snapshot: dict[str, Any]


class AnnouncementRepository(Protocol):
    def add(self, announcement: Announcement) -> None: ...
    def save(self, announcement: Announcement) -> None: ...
    def get(self, announcement_id: UUID, community_id: UUID) -> Announcement | None: ...
    def list(self, community_id: UUID, search: AnnouncementSearch) -> Sequence[Announcement]: ...
    def add_version(
        self, announcement_id: UUID, community_id: UUID, version: AnnouncementVersion
    ) -> None: ...
    def versions(
        self, announcement_id: UUID, community_id: UUID
    ) -> Sequence[AnnouncementVersion]: ...
    def add_review(
        self, announcement_id: UUID, community_id: UUID, review: AnnouncementReview
    ) -> None: ...
    def add_audience_snapshot(
        self, announcement_id: UUID, community_id: UUID, snapshot: AudienceSnapshot
    ) -> None: ...
    def latest_audience_snapshot(
        self, announcement_id: UUID, community_id: UUID
    ) -> AudienceSnapshot | None: ...
    def add_withdrawal(
        self, announcement_id: UUID, community_id: UUID, withdrawal: AnnouncementWithdrawal
    ) -> None: ...
    def list_due_scheduled(self, now: datetime, limit: int) -> Sequence[Announcement]: ...
    def latest_review(
        self, announcement_id: UUID, community_id: UUID, action: str
    ) -> AnnouncementReview | None: ...


class IdempotencyPort(Protocol):
    def get(self, actor_id: UUID, operation: str, key: str) -> IdempotencyRecord | None: ...
    def add(self, record: IdempotencyRecord) -> None: ...


class ConfirmationPort(Protocol):
    def consume(
        self,
        *,
        approval_ref: str | None,
        token: str,
        actor_id: UUID,
        action: str,
        parameter_hash: str,
        request_id: str,
    ) -> None: ...


class AudienceResolverPort(Protocol):
    def resolve(
        self, *, community_id: UUID, condition: dict[str, list[str]], request_id: str
    ) -> AudienceSnapshot: ...


class AuditPort(Protocol):
    def add(self, **event: Any) -> None: ...


class MessagePort(Protocol):
    def enqueue(
        self,
        *,
        community_id: UUID,
        receiver_id: UUID,
        event_type: str,
        resource_id: UUID,
        request_id: str,
        created_at: datetime,
    ) -> None: ...


class DraftSuggestionPort(Protocol):
    def suggest(self, *, prompt: str, context: RequestContext) -> dict[str, Any]: ...


class AnnouncementUnitOfWork(Protocol):
    announcements: AnnouncementRepository
    idempotency: IdempotencyPort
    confirmations: ConfirmationPort
    audiences: AudienceResolverPort
    audit: AuditPort
    messages: MessagePort

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


AnnouncementUnitOfWorkFactory = Callable[[], AnnouncementUnitOfWork]
