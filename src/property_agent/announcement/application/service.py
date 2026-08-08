import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
    EditAnnouncementCommand,
    ReviewActionCommand,
)
from property_agent.announcement.application.ports import (
    AnnouncementUnitOfWork,
    AnnouncementUnitOfWorkFactory,
    IdempotencyRecord,
)
from property_agent.announcement.domain.entities import (
    Announcement,
    AnnouncementReview,
    AnnouncementVersion,
    AudienceSnapshot,
)
from property_agent.announcement.domain.enums import (
    CREATE_ROLES,
    READ_ROLES,
    AnnouncementAction,
    AnnouncementStatus,
)
from property_agent.announcement.domain.errors import (
    empty_audience,
    idempotency_conflict,
    not_found,
    version_conflict,
)
from property_agent.announcement.domain.policies import (
    HIGH_RISK_CATEGORIES,
    normalize_audience_condition,
    validate_category,
)
from property_agent.platform.context import RequestContext
from property_agent.platform.roles import Role
from property_agent.platform.validation import (
    new_business_no,
    require_idempotency_key,
    require_role,
    required_text,
    validate_pagination,
)


def canonical_hash(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, tuple | frozenset):
            return [normalize(entry) for entry in item]
        if isinstance(item, dict):
            return {key: normalize(entry) for key, entry in sorted(item.items())}
        if isinstance(item, list):
            return [normalize(entry) for entry in item]
        return item

    payload = json.dumps(
        normalize(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AnnouncementService:
    """The sole application entry point for announcement mutations."""

    def __init__(self, unit_of_work_factory: AnnouncementUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_draft(
        self, command: CreateAnnouncementCommand, context: RequestContext, *, idempotency_key: str
    ) -> Announcement:
        require_role(context, *CREATE_ROLES)
        require_idempotency_key(idempotency_key)
        title, body, category, audience = self._validated_content(command)
        request_hash = canonical_hash(asdict(command))
        operation = "ANNOUNCEMENT_CREATE"
        with self._unit_of_work_factory() as uow:
            replay = self._replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            now = datetime.now(UTC)
            announcement = Announcement(
                id=uuid4(),
                community_id=context.community_id,
                business_no=new_business_no(now, "AN"),
                title=title,
                body=body,
                category=category,
                audience_condition=audience,
                created_by=context.actor_id,
                create_idempotency_key=idempotency_key,
                scheduled_at=command.scheduled_at,
                manager_recheck_required=category in HIGH_RISK_CATEGORIES,
                created_at=now,
                updated_at=now,
            )
            uow.announcements.add(announcement)
            self._add_version(uow, announcement, context, command.source, now)
            self._record_idempotency(
                uow, announcement, context, operation, idempotency_key, request_hash
            )
            self._audit(
                uow, announcement, context, AnnouncementAction.CREATE, {"category": category}, now
            )
            uow.commit()
            return announcement

    def edit_draft(
        self,
        announcement_id: UUID,
        command: EditAnnouncementCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> Announcement:
        require_role(context, *CREATE_ROLES)
        require_idempotency_key(idempotency_key)
        title, body, category, audience = self._validated_content(command)
        operation = "ANNOUNCEMENT_EDIT"
        request_hash = canonical_hash({"announcement_id": announcement_id, **asdict(command)})
        with self._unit_of_work_factory() as uow:
            replay = self._replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            announcement = self._get(uow, announcement_id, context)
            if announcement.version != command.expected_version:
                raise version_conflict(announcement.version)
            now = datetime.now(UTC)
            announcement.edit(
                title=title, body=body, category=category, audience_condition=audience, now=now
            )
            announcement.manager_recheck_required = category in HIGH_RISK_CATEGORIES
            uow.announcements.save(announcement)
            self._add_version(uow, announcement, context, command.source, now)
            self._record_idempotency(
                uow, announcement, context, operation, idempotency_key, request_hash
            )
            self._audit(
                uow, announcement, context, AnnouncementAction.EDIT, {"category": category}, now
            )
            uow.commit()
            return announcement

    def get(self, announcement_id: UUID, context: RequestContext) -> Announcement:
        require_role(context, *READ_ROLES)
        with self._unit_of_work_factory() as uow:
            return self._get(uow, announcement_id, context)

    def preview_audience(self, announcement_id: UUID, context: RequestContext) -> AudienceSnapshot:
        require_role(context, *CREATE_ROLES)
        with self._unit_of_work_factory() as uow:
            announcement = self._get(uow, announcement_id, context)
            return uow.audiences.resolve(
                community_id=context.community_id,
                condition=announcement.audience_condition,
                request_id=context.request_id,
            )

    def submit_review(
        self,
        announcement_id: UUID,
        command: ReviewActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> Announcement:
        require_role(context, *CREATE_ROLES)
        if command.action != AnnouncementAction.SUBMIT_REVIEW:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("submit_review requires the SUBMIT_REVIEW action.")
        return self._review_action(
            announcement_id, command, context, idempotency_key=idempotency_key
        )

    def review_action(
        self,
        announcement_id: UUID,
        command: ReviewActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> Announcement:
        require_role(context, Role.MANAGER)
        if command.action not in {AnnouncementAction.APPROVE, AnnouncementAction.REJECT}:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("Only APPROVE and REJECT are review actions.")
        return self._review_action(
            announcement_id, command, context, idempotency_key=idempotency_key
        )

    def _review_action(
        self,
        announcement_id: UUID,
        command: ReviewActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> Announcement:
        require_idempotency_key(idempotency_key)
        operation = f"ANNOUNCEMENT_{command.action.value}"
        request_hash = canonical_hash({"announcement_id": announcement_id, **asdict(command)})
        with self._unit_of_work_factory() as uow:
            replay = self._replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            announcement = self._get(uow, announcement_id, context)
            if announcement.version != command.expected_version:
                raise version_conflict(announcement.version)
            now = datetime.now(UTC)
            if command.action == AnnouncementAction.SUBMIT_REVIEW:
                snapshot = uow.audiences.resolve(
                    community_id=context.community_id,
                    condition=announcement.audience_condition,
                    request_id=context.request_id,
                )
                if snapshot.count <= 0 or not snapshot.member_ids:
                    raise empty_audience()
                uow.announcements.add_audience_snapshot(
                    announcement.id, context.community_id, snapshot
                )
            reason = None
            if command.action == AnnouncementAction.REJECT:
                reason = required_text(command.reason, "A rejection reason is required.")
            announcement.transition(command.action, now=now)
            uow.announcements.add_review(
                announcement.id,
                context.community_id,
                AnnouncementReview(command.action, context.actor_id, reason, now),
            )
            uow.announcements.save(announcement)
            self._record_idempotency(
                uow, announcement, context, operation, idempotency_key, request_hash
            )
            self._audit(
                uow,
                announcement,
                context,
                command.action,
                {
                    "reason": reason,
                    "manager_recheck_required": announcement.manager_recheck_required,
                },
                now,
            )
            uow.commit()
            return announcement

    def search(self, search: AnnouncementSearch, context: RequestContext) -> list[Announcement]:
        require_role(context, *READ_ROLES)
        validate_pagination(search.limit, search.offset)
        with self._unit_of_work_factory() as uow:
            return list(uow.announcements.list(context.community_id, search))

    def versions(self, announcement_id: UUID, context: RequestContext) -> list[AnnouncementVersion]:
        require_role(context, *READ_ROLES)
        with self._unit_of_work_factory() as uow:
            announcement = self._get(uow, announcement_id, context)
            return list(uow.announcements.versions(announcement.id, context.community_id))

    def available_actions(
        self, announcement: Announcement, context: RequestContext
    ) -> list[AnnouncementAction]:
        if not context.has_any_role(*READ_ROLES):
            return []
        actions = list(announcement.state_actions())
        if not context.has_any_role(*CREATE_ROLES):
            actions = []
        if not context.has_any_role(*CREATE_ROLES):
            return []
        if not context.has_any_role(*READ_ROLES):
            return []
        return actions

    @staticmethod
    def _validated_content(
        command: CreateAnnouncementCommand | EditAnnouncementCommand,
    ) -> tuple[str, str, str, dict[str, list[str]]]:
        title = required_text(command.title, "title is required.")
        body = required_text(command.body, "body is required.")
        if len(title) > 128:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("title must not exceed 128 characters.")
        category = validate_category(command.category)
        return title, body, category, normalize_audience_condition(command.audience_condition)

    @staticmethod
    def _get(
        uow: AnnouncementUnitOfWork, announcement_id: UUID, context: RequestContext
    ) -> Announcement:
        announcement = uow.announcements.get(announcement_id, context.community_id)
        if announcement is None:
            raise not_found()
        return announcement

    def _replay(
        self,
        uow: AnnouncementUnitOfWork,
        context: RequestContext,
        operation: str,
        key: str,
        request_hash: str,
    ) -> Announcement | None:
        existing = uow.idempotency.get(context.actor_id, operation, key)
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise idempotency_conflict()
        return self._from_snapshot(existing.response_snapshot)

    def _record_idempotency(
        self,
        uow: AnnouncementUnitOfWork,
        announcement: Announcement,
        context: RequestContext,
        operation: str,
        key: str,
        request_hash: str,
    ) -> None:
        uow.idempotency.add(
            IdempotencyRecord(
                context.actor_id,
                operation,
                key,
                request_hash,
                announcement.id,
                self._snapshot(announcement),
            )
        )

    @staticmethod
    def _add_version(
        uow: AnnouncementUnitOfWork,
        announcement: Announcement,
        context: RequestContext,
        source,
        now: datetime,
    ) -> None:
        uow.announcements.add_version(
            announcement.id,
            announcement.community_id,
            AnnouncementVersion(
                announcement.version,
                announcement.title,
                announcement.body,
                announcement.category,
                announcement.audience_condition,
                context.actor_id,
                source,
                now,
            ),
        )

    @staticmethod
    def _audit(
        uow: AnnouncementUnitOfWork,
        announcement: Announcement,
        context: RequestContext,
        action: AnnouncementAction,
        parameters: dict[str, Any],
        now: datetime,
    ) -> None:
        uow.audit.add(
            community_id=context.community_id,
            actor_id=context.actor_id,
            action=action.value,
            resource_type="ANNOUNCEMENT",
            resource_id=announcement.id,
            parameter_summary=parameters,
            request_id=context.request_id,
            created_at=now,
        )

    @staticmethod
    def _snapshot(announcement: Announcement) -> dict[str, Any]:
        return {
            "id": str(announcement.id),
            "community_id": str(announcement.community_id),
            "business_no": announcement.business_no,
            "title": announcement.title,
            "body": announcement.body,
            "category": announcement.category,
            "audience_condition": announcement.audience_condition,
            "created_by": str(announcement.created_by),
            "create_idempotency_key": announcement.create_idempotency_key,
            "scheduled_at": announcement.scheduled_at.isoformat()
            if announcement.scheduled_at
            else None,
            "status": announcement.status.value,
            "version": announcement.version,
            "manager_recheck_required": announcement.manager_recheck_required,
            "published_at": announcement.published_at.isoformat()
            if announcement.published_at
            else None,
            "withdrawn_at": announcement.withdrawn_at.isoformat()
            if announcement.withdrawn_at
            else None,
            "created_at": announcement.created_at.isoformat(),
            "updated_at": announcement.updated_at.isoformat(),
        }

    @staticmethod
    def _from_snapshot(snapshot: dict[str, Any]) -> Announcement:
        return Announcement(
            id=UUID(snapshot["id"]),
            community_id=UUID(snapshot["community_id"]),
            business_no=snapshot["business_no"],
            title=snapshot["title"],
            body=snapshot["body"],
            category=snapshot["category"],
            audience_condition=snapshot["audience_condition"],
            created_by=UUID(snapshot["created_by"]),
            create_idempotency_key=snapshot["create_idempotency_key"],
            scheduled_at=datetime.fromisoformat(snapshot["scheduled_at"])
            if snapshot["scheduled_at"]
            else None,
            status=AnnouncementStatus(snapshot["status"]),
            version=snapshot["version"],
            manager_recheck_required=snapshot["manager_recheck_required"],
            published_at=datetime.fromisoformat(snapshot["published_at"])
            if snapshot["published_at"]
            else None,
            withdrawn_at=datetime.fromisoformat(snapshot["withdrawn_at"])
            if snapshot["withdrawn_at"]
            else None,
            created_at=datetime.fromisoformat(snapshot["created_at"]),
            updated_at=datetime.fromisoformat(snapshot["updated_at"]),
        )
