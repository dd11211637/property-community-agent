import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.infrastructure.database import create_session_factory
from property_agent.announcement.infrastructure.models import (
    AnnouncementAudienceSnapshotModel,
    AnnouncementModel,
    AnnouncementReviewModel,
    AnnouncementVersionModel,
    AnnouncementWithdrawalModel,
    Base,
)
from property_agent.announcement.infrastructure.uow import SqlAlchemyAnnouncementUnitOfWork
from property_agent.platform.context import RequestContext
from property_agent.platform.roles import Role
from tests.announcement.support import FakeAudienceResolver, FakeAudit, FakeIdempotency, FakeState

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_URL, reason="requires TEST_POSTGRES_URL and a dedicated PostgreSQL database"
    ),
]

ANNOUNCEMENT_TABLES = [
    AnnouncementModel.__table__,
    AnnouncementVersionModel.__table__,
    AnnouncementReviewModel.__table__,
    AnnouncementAudienceSnapshotModel.__table__,
    AnnouncementWithdrawalModel.__table__,
]


def test_repository_persists_and_scopes_announcements() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine, tables=ANNOUNCEMENT_TABLES)
    state = FakeState()
    members = (uuid4(),)
    sessions = create_session_factory(POSTGRES_URL)

    def factory():
        return SqlAlchemyAnnouncementUnitOfWork(
            sessions,
            lambda session: SimpleNamespace(
                idempotency=FakeIdempotency(state),
                confirmations=None,
                audiences=FakeAudienceResolver(members),
                audit=FakeAudit(state),
                messages=None,
            ),
        )

    try:
        service = AnnouncementService(factory)
        community = uuid4()
        manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "pg")
        item = service.create_draft(
            CreateAnnouncementCommand("公告", "正文", "GENERAL", {"building_ids": ["B1"]}),
            manager,
            idempotency_key="pg-create",
        )
        assert service.get(item.id, manager).title == "公告"
        stranger = RequestContext(uuid4(), uuid4(), frozenset({Role.MANAGER}), "other")
        assert service.search(AnnouncementSearch(), stranger) == []
    finally:
        Base.metadata.drop_all(engine, tables=ANNOUNCEMENT_TABLES)
        engine.dispose()
