from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
    EditAnnouncementCommand,
    ReviewActionCommand,
    ScheduleAnnouncementCommand,
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
    AnnouncementWithdrawal,
    AudienceSnapshot,
)
from property_agent.announcement.domain.enums import (
    CREATE_ROLES,
    READ_ROLES,
    AnnouncementAction,
    AnnouncementStatus,
)
from property_agent.announcement.domain.errors import (
    confirmation_required,
    empty_audience,
    forbidden,
    idempotency_conflict,
    not_found,
    version_conflict,
)
from property_agent.announcement.domain.policies import (
    HIGH_RISK_CATEGORIES,
    normalize_audience_condition,
    validate_category,
)
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.context import RequestContext
from property_agent.platform.roles import Role
from property_agent.platform.validation import (
    new_business_no,
    require_idempotency_key,
    require_role,
    required_text,
    validate_pagination,
)

# ``canonical_hash`` is re-exported so existing imports keep working. It is the
# single system-wide hash (PRD 12.2): a confirmation token minted by
# ``POST /api/confirmations`` must hash the publish parameters exactly the way
# this service does, otherwise cross-module二次确认 silently breaks.
__all__ = ["AnnouncementService", "canonical_hash"]


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
            announcement = self._get(uow, announcement_id, context)
            self._ensure_resident_visibility(uow, announcement, context)
            return announcement

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
        self._require_manager(context, announcement_id, command.action.value)
        if command.action not in {AnnouncementAction.APPROVE, AnnouncementAction.REJECT}:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("Only APPROVE and REJECT are review actions.")
        return self._review_action(
            announcement_id, command, context, idempotency_key=idempotency_key
        )

    def publish(
        self,
        announcement_id: UUID,
        command: ReviewActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> Announcement:
        self._require_manager(context, announcement_id, command.action.value)
        require_idempotency_key(idempotency_key)
        if command.action != AnnouncementAction.PUBLISH:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("publish requires the PUBLISH action.")
        if not command.confirmation_token or not command.confirmation_token.strip():
            raise confirmation_required()
        operation = "ANNOUNCEMENT_PUBLISH"
        confirmation_hash = canonical_hash(
            {
                "announcement_id": announcement_id,
                "expected_version": command.expected_version,
                "action": command.action,
            }
        )
        request_hash = canonical_hash({"confirmation": confirmation_hash, "key": idempotency_key})
        with self._unit_of_work_factory() as uow:
            replay = self._replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            announcement = self._get(uow, announcement_id, context)
            if announcement.version != command.expected_version:
                raise version_conflict(announcement.version)
            snapshot = uow.audiences.resolve(
                community_id=context.community_id,
                condition=announcement.audience_condition,
                request_id=context.request_id,
            )
            if snapshot.count <= 0 or not snapshot.member_ids:
                raise empty_audience()
            uow.confirmations.consume(
                approval_ref=command.approval_ref,
                token=command.confirmation_token.strip(),
                actor_id=context.actor_id,
                action=operation,
                parameter_hash=confirmation_hash,
                request_id=context.request_id,
            )
            now = datetime.now(UTC)
            announcement.transition(AnnouncementAction.PUBLISH, now=now)
            uow.announcements.add_audience_snapshot(announcement.id, context.community_id, snapshot)
            uow.announcements.add_review(
                announcement.id,
                context.community_id,
                AnnouncementReview(AnnouncementAction.PUBLISH, context.actor_id, None, now),
            )
            uow.announcements.save(announcement)
            for receiver_id in snapshot.member_ids:
                uow.messages.enqueue(
                    community_id=context.community_id,
                    receiver_id=receiver_id,
                    event_type="ANNOUNCEMENT_PUBLISHED",
                    resource_id=announcement.id,
                    request_id=context.request_id,
                    created_at=now,
                )
            self._record_idempotency(
                uow, announcement, context, operation, idempotency_key, request_hash
            )
            self._audit(
                uow,
                announcement,
                context,
                AnnouncementAction.PUBLISH,
                {"audience_count": snapshot.count, "version": announcement.version},
                now,
            )
            uow.commit()
            return announcement

    def schedule_publish(
        self,
        announcement_id: UUID,
        command: ScheduleAnnouncementCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> Announcement:
        self._require_manager(context, announcement_id, AnnouncementAction.SCHEDULE.value)
        require_idempotency_key(idempotency_key)
        now = datetime.now(UTC)
        scheduled_at = command.scheduled_at
        if scheduled_at.tzinfo is None:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("scheduled_at must include a timezone.")
        scheduled_at = scheduled_at.astimezone(UTC)
        if scheduled_at <= now:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("scheduled_at must be in the future.")
        operation = "ANNOUNCEMENT_SCHEDULE"
        confirmation_hash = canonical_hash(
            {
                "announcement_id": announcement_id,
                "expected_version": command.expected_version,
                "scheduled_at": scheduled_at,
            }
        )
        request_hash = canonical_hash({"confirmation": confirmation_hash, "key": idempotency_key})
        with self._unit_of_work_factory() as uow:
            replay = self._replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            announcement = self._get(uow, announcement_id, context)
            if announcement.version != command.expected_version:
                raise version_conflict(announcement.version)
            snapshot = uow.audiences.resolve(
                community_id=context.community_id,
                condition=announcement.audience_condition,
                request_id=context.request_id,
            )
            if snapshot.count <= 0 or not snapshot.member_ids:
                raise empty_audience()
            uow.confirmations.consume(
                approval_ref=command.approval_ref,
                token=command.confirmation_token.strip(),
                actor_id=context.actor_id,
                action=operation,
                parameter_hash=confirmation_hash,
                request_id=context.request_id,
            )
            announcement.schedule(scheduled_at=scheduled_at, now=now)
            uow.announcements.add_audience_snapshot(announcement.id, context.community_id, snapshot)
            uow.announcements.add_review(
                announcement.id,
                context.community_id,
                AnnouncementReview(AnnouncementAction.SCHEDULE, context.actor_id, None, now),
            )
            uow.announcements.save(announcement)
            self._record_idempotency(
                uow, announcement, context, operation, idempotency_key, request_hash
            )
            self._audit(
                uow,
                announcement,
                context,
                AnnouncementAction.SCHEDULE,
                {"scheduled_at": scheduled_at.isoformat(), "audience_count": snapshot.count},
                now,
            )
            uow.commit()
            return announcement

    def publish_due(self, *, now: datetime | None = None, limit: int = 50) -> int:
        """Publish pre-authorized scheduled announcements from persistent state."""
        due_at = (now or datetime.now(UTC)).astimezone(UTC)
        published_count = 0
        with self._unit_of_work_factory() as uow:
            for announcement in uow.announcements.list_due_scheduled(due_at, limit):
                authorization = uow.announcements.latest_review(
                    announcement.id,
                    announcement.community_id,
                    AnnouncementAction.SCHEDULE.value,
                )
                snapshot = uow.announcements.latest_audience_snapshot(
                    announcement.id, announcement.community_id
                )
                if authorization is None or snapshot is None or not snapshot.member_ids:
                    continue
                announcement.transition(AnnouncementAction.PUBLISH, now=due_at)
                uow.announcements.add_review(
                    announcement.id,
                    announcement.community_id,
                    AnnouncementReview(
                        AnnouncementAction.PUBLISH,
                        authorization.reviewer_id,
                        "SCHEDULED_EXECUTION",
                        due_at,
                    ),
                )
                uow.announcements.save(announcement)
                for receiver_id in snapshot.member_ids:
                    uow.messages.enqueue(
                        community_id=announcement.community_id,
                        receiver_id=receiver_id,
                        event_type="ANNOUNCEMENT_PUBLISHED",
                        resource_id=announcement.id,
                        request_id=f"scheduled:{announcement.id}",
                        created_at=due_at,
                    )
                system_context = RequestContext(
                    authorization.reviewer_id,
                    announcement.community_id,
                    frozenset({Role.MANAGER}),
                    f"scheduled:{announcement.id}",
                )
                self._audit(
                    uow,
                    announcement,
                    system_context,
                    AnnouncementAction.PUBLISH,
                    {"scheduled_execution": True, "audience_count": snapshot.count},
                    due_at,
                )
                published_count += 1
            uow.commit()
        return published_count

    def withdraw(
        self,
        announcement_id: UUID,
        command: ReviewActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> Announcement:
        self._require_manager(context, announcement_id, command.action.value)
        require_idempotency_key(idempotency_key)
        if command.action != AnnouncementAction.WITHDRAW:
            from property_agent.announcement.domain.errors import validation_error

            raise validation_error("withdraw requires the WITHDRAW action.")
        operation = "ANNOUNCEMENT_WITHDRAW"
        request_hash = canonical_hash({"announcement_id": announcement_id, **asdict(command)})
        with self._unit_of_work_factory() as uow:
            replay = self._replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            announcement = self._get(uow, announcement_id, context)
            if announcement.version != command.expected_version:
                raise version_conflict(announcement.version)
            reason = required_text(command.reason, "A withdrawal reason is required.")
            now = datetime.now(UTC)
            prior_version = announcement.version
            announcement.transition(AnnouncementAction.WITHDRAW, now=now)
            uow.announcements.add_withdrawal(
                announcement.id,
                context.community_id,
                AnnouncementWithdrawal(context.actor_id, reason, prior_version, now),
            )
            uow.announcements.save(announcement)
            audience = uow.announcements.latest_audience_snapshot(
                announcement.id, context.community_id
            )
            if audience is not None:
                for receiver_id in audience.member_ids:
                    uow.messages.enqueue(
                        community_id=context.community_id,
                        receiver_id=receiver_id,
                        event_type="ANNOUNCEMENT_WITHDRAWN",
                        resource_id=announcement.id,
                        request_id=context.request_id,
                        created_at=now,
                    )
            self._record_idempotency(
                uow, announcement, context, operation, idempotency_key, request_hash
            )
            self._audit(
                uow,
                announcement,
                context,
                AnnouncementAction.WITHDRAW,
                {
                    "reason": reason,
                    "audience_count": audience.count if audience is not None else 0,
                },
                now,
            )
            uow.commit()
            return announcement

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
            if self._is_resident_only(context):
                published = uow.announcements.list(
                    context.community_id,
                    AnnouncementSearch((AnnouncementStatus.PUBLISHED.value,), 10_000, 0),
                )
                visible = [
                    item
                    for item in published
                    if self._resident_in_frozen_audience(uow, item, context.actor_id)
                ]
                return visible[search.offset : search.offset + search.limit]
            return list(uow.announcements.list(context.community_id, search))

    def versions(self, announcement_id: UUID, context: RequestContext) -> list[AnnouncementVersion]:
        require_role(context, Role.CUSTOMER_SERVICE, Role.MANAGER)
        with self._unit_of_work_factory() as uow:
            announcement = self._get(uow, announcement_id, context)
            return list(uow.announcements.versions(announcement.id, context.community_id))

    def available_actions(
        self, announcement: Announcement, context: RequestContext
    ) -> list[AnnouncementAction]:
        if not context.has_any_role(*READ_ROLES):
            return []
        actions: list[AnnouncementAction] = []
        if announcement.status in {AnnouncementStatus.DRAFT, AnnouncementStatus.REJECTED}:
            if context.has_any_role(*CREATE_ROLES):
                actions.append(AnnouncementAction.EDIT)
                actions.append(AnnouncementAction.SUBMIT_REVIEW)
        if context.has_any_role(Role.MANAGER):
            actions.extend(
                action for action in announcement.state_actions() if action not in actions
            )
            if (
                announcement.status == AnnouncementStatus.APPROVED
                and AnnouncementAction.SCHEDULE not in actions
            ):
                actions.append(AnnouncementAction.SCHEDULE)
        return actions

    @staticmethod
    def _is_resident_only(context: RequestContext) -> bool:
        return context.has_any_role(Role.RESIDENT) and not context.has_any_role(
            Role.CUSTOMER_SERVICE, Role.MANAGER
        )

    def _ensure_resident_visibility(
        self,
        uow: AnnouncementUnitOfWork,
        announcement: Announcement,
        context: RequestContext,
    ) -> None:
        if not self._is_resident_only(context):
            return
        if (
            announcement.status != AnnouncementStatus.PUBLISHED
            or not self._resident_in_frozen_audience(uow, announcement, context.actor_id)
        ):
            raise not_found()

    @staticmethod
    def _resident_in_frozen_audience(
        uow: AnnouncementUnitOfWork, announcement: Announcement, actor_id: UUID
    ) -> bool:
        snapshot = uow.announcements.latest_audience_snapshot(
            announcement.id, announcement.community_id
        )
        return snapshot is not None and actor_id in snapshot.member_ids

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

    def _require_manager(self, context: RequestContext, announcement_id: UUID, action: str) -> None:
        if context.has_any_role(Role.MANAGER):
            return
        now = datetime.now(UTC)
        with self._unit_of_work_factory() as uow:
            uow.audit.add(
                community_id=context.community_id,
                actor_id=context.actor_id,
                action="UNAUTHORIZED_ANNOUNCEMENT_ACTION",
                resource_type="ANNOUNCEMENT",
                resource_id=announcement_id,
                parameter_summary={"attempted_action": action},
                request_id=context.request_id,
                created_at=now,
            )
            uow.commit()
        raise forbidden()

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
