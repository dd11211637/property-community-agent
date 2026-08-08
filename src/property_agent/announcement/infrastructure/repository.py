from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from property_agent.announcement.application.commands import AnnouncementSearch
from property_agent.announcement.domain.entities import (
    Announcement,
    AnnouncementReview,
    AnnouncementVersion,
    AnnouncementWithdrawal,
    AudienceSnapshot,
)
from property_agent.announcement.domain.enums import AnnouncementStatus, VersionSource
from property_agent.announcement.domain.errors import version_conflict
from property_agent.announcement.infrastructure.models import (
    AnnouncementAudienceSnapshotModel,
    AnnouncementModel,
    AnnouncementReviewModel,
    AnnouncementVersionModel,
    AnnouncementWithdrawalModel,
)


class SqlAlchemyAnnouncementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, announcement: Announcement) -> None:
        self._session.add(self._to_model(announcement))

    def save(self, announcement: Announcement) -> None:
        result = self._session.execute(
            update(AnnouncementModel)
            .where(
                AnnouncementModel.id == announcement.id,
                AnnouncementModel.community_id == announcement.community_id,
                AnnouncementModel.version == announcement.version - 1,
            )
            .values(
                title=announcement.title,
                body=announcement.body,
                category=announcement.category,
                audience_condition=announcement.audience_condition,
                status=announcement.status.value,
                version=announcement.version,
                manager_recheck_required=announcement.manager_recheck_required,
                published_at=announcement.published_at,
                withdrawn_at=announcement.withdrawn_at,
                updated_at=announcement.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            current = self._session.scalar(
                select(AnnouncementModel.version).where(AnnouncementModel.id == announcement.id)
            )
            raise version_conflict(current or announcement.version)

    def get(self, announcement_id: UUID, community_id: UUID) -> Announcement | None:
        model = self._session.scalar(
            select(AnnouncementModel).where(
                AnnouncementModel.id == announcement_id,
                AnnouncementModel.community_id == community_id,
            )
        )
        return self._to_domain(model) if model else None

    def list(self, community_id: UUID, search: AnnouncementSearch) -> Sequence[Announcement]:
        stmt = (
            select(AnnouncementModel)
            .where(AnnouncementModel.community_id == community_id)
            .order_by(AnnouncementModel.created_at.desc())
            .offset(search.offset)
            .limit(search.limit)
        )
        if search.statuses:
            stmt = stmt.where(AnnouncementModel.status.in_(search.statuses))
        return [self._to_domain(item) for item in self._session.scalars(stmt).all()]

    def add_version(
        self, announcement_id: UUID, community_id: UUID, version: AnnouncementVersion
    ) -> None:
        self._session.add(
            AnnouncementVersionModel(
                id=uuid4(),
                community_id=community_id,
                announcement_id=announcement_id,
                version_no=version.version_no,
                title=version.title,
                body=version.body,
                category=version.category,
                audience_condition=version.audience_condition,
                operator_id=version.operator_id,
                source=version.source.value,
                created_at=version.created_at,
            )
        )

    def versions(self, announcement_id: UUID, community_id: UUID) -> Sequence[AnnouncementVersion]:
        records = self._session.scalars(
            select(AnnouncementVersionModel)
            .where(
                AnnouncementVersionModel.announcement_id == announcement_id,
                AnnouncementVersionModel.community_id == community_id,
            )
            .order_by(AnnouncementVersionModel.version_no)
        ).all()
        return [
            AnnouncementVersion(
                item.version_no,
                item.title,
                item.body,
                item.category,
                item.audience_condition,
                item.operator_id,
                VersionSource(item.source),
                item.created_at,
            )
            for item in records
        ]

    def add_review(
        self, announcement_id: UUID, community_id: UUID, review: AnnouncementReview
    ) -> None:
        self._session.add(
            AnnouncementReviewModel(
                id=uuid4(),
                community_id=community_id,
                announcement_id=announcement_id,
                action=review.action.value,
                reviewer_id=review.reviewer_id,
                reason=review.reason,
                created_at=review.created_at,
            )
        )

    def add_audience_snapshot(
        self, announcement_id: UUID, community_id: UUID, snapshot: AudienceSnapshot
    ) -> None:
        self._session.add(
            AnnouncementAudienceSnapshotModel(
                id=uuid4(),
                community_id=community_id,
                announcement_id=announcement_id,
                condition=snapshot.condition,
                member_ids=[str(item) for item in snapshot.member_ids],
                recipient_count=snapshot.count,
                samples=list(snapshot.samples),
                created_at=snapshot.generated_at,
            )
        )

    def latest_audience_snapshot(
        self, announcement_id: UUID, community_id: UUID
    ) -> AudienceSnapshot | None:
        item = self._session.scalar(
            select(AnnouncementAudienceSnapshotModel)
            .where(
                AnnouncementAudienceSnapshotModel.announcement_id == announcement_id,
                AnnouncementAudienceSnapshotModel.community_id == community_id,
            )
            .order_by(AnnouncementAudienceSnapshotModel.created_at.desc())
        )
        return (
            AudienceSnapshot(
                item.condition,
                tuple(UUID(value) for value in item.member_ids),
                item.recipient_count,
                tuple(item.samples),
                item.created_at,
            )
            if item
            else None
        )

    def add_withdrawal(
        self, announcement_id: UUID, community_id: UUID, withdrawal: AnnouncementWithdrawal
    ) -> None:
        self._session.add(
            AnnouncementWithdrawalModel(
                id=uuid4(),
                community_id=community_id,
                announcement_id=announcement_id,
                withdrawn_by=withdrawal.withdrawn_by,
                reason=withdrawal.reason,
                version_no=withdrawal.version_no,
                created_at=withdrawal.created_at,
            )
        )

    @staticmethod
    def _to_model(item: Announcement) -> AnnouncementModel:
        return AnnouncementModel(
            id=item.id,
            community_id=item.community_id,
            business_no=item.business_no,
            title=item.title,
            body=item.body,
            category=item.category,
            audience_condition=item.audience_condition,
            created_by=item.created_by,
            create_idempotency_key=item.create_idempotency_key,
            scheduled_at=item.scheduled_at,
            status=item.status.value,
            version=item.version,
            manager_recheck_required=item.manager_recheck_required,
            published_at=item.published_at,
            withdrawn_at=item.withdrawn_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _to_domain(item: AnnouncementModel) -> Announcement:
        return Announcement(
            id=item.id,
            community_id=item.community_id,
            business_no=item.business_no,
            title=item.title,
            body=item.body,
            category=item.category,
            audience_condition=item.audience_condition,
            created_by=item.created_by,
            create_idempotency_key=item.create_idempotency_key,
            scheduled_at=item.scheduled_at,
            status=AnnouncementStatus(item.status),
            version=item.version,
            manager_recheck_required=item.manager_recheck_required,
            published_at=item.published_at,
            withdrawn_at=item.withdrawn_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
