from uuid import uuid4

import pytest

from property_agent.announcement.application.commands import (
    CreateAnnouncementCommand,
    ReviewActionCommand,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.enums import AnnouncementAction, AnnouncementStatus
from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.roles import Role
from tests.announcement.support import Harness


def _create(service, context):
    return service.create_draft(
        CreateAnnouncementCommand("检修", "B1 检修", "GENERAL", {"building_ids": ["B1"]}),
        context,
        idempotency_key="create",
    )


def test_review_flow_freezes_audience_and_requires_manager() -> None:
    members = (uuid4(), uuid4())
    harness = Harness(audience_members=members)
    service = AnnouncementService(harness.uow)
    community_id = uuid4()
    customer = RequestContext(uuid4(), community_id, frozenset({Role.CUSTOMER_SERVICE}), "cs")
    manager = RequestContext(uuid4(), community_id, frozenset({Role.MANAGER}), "manager")
    item = _create(service, customer)
    item = service.submit_review(
        item.id,
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, item.version),
        customer,
        idempotency_key="submit",
    )
    assert item.status == AnnouncementStatus.PENDING_REVIEW
    assert harness.state.snapshots[item.id][-1].member_ids == members
    item = service.review_action(
        item.id,
        ReviewActionCommand(AnnouncementAction.APPROVE, item.version),
        manager,
        idempotency_key="approve",
    )
    assert item.status == AnnouncementStatus.APPROVED
    assert [review.action.value for review in harness.state.reviews] == ["SUBMIT_REVIEW", "APPROVE"]
    with pytest.raises(BusinessError) as exc:
        service.review_action(
            item.id,
            ReviewActionCommand(AnnouncementAction.REJECT, item.version, "too late"),
            customer,
            idempotency_key="forbidden",
        )
    assert exc.value.code == "FORBIDDEN"
    assert harness.state.audits[-1]["action"] == "UNAUTHORIZED_ANNOUNCEMENT_ACTION"


def test_empty_audience_rejection_reason_and_version_conflict() -> None:
    empty = Harness()
    service = AnnouncementService(empty.uow)
    community_id = uuid4()
    customer = RequestContext(uuid4(), community_id, frozenset({Role.CUSTOMER_SERVICE}), "cs")
    manager = RequestContext(uuid4(), community_id, frozenset({Role.MANAGER}), "manager")
    item = _create(service, customer)
    with pytest.raises(BusinessError) as exc:
        service.submit_review(
            item.id,
            ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, item.version),
            customer,
            idempotency_key="empty",
        )
    assert exc.value.code == "EMPTY_AUDIENCE"
    empty.audiences.member_ids = (uuid4(),)
    item = service.submit_review(
        item.id,
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, item.version),
        customer,
        idempotency_key="submit",
    )
    with pytest.raises(BusinessError) as exc:
        service.review_action(
            item.id,
            ReviewActionCommand(AnnouncementAction.REJECT, item.version),
            manager,
            idempotency_key="no-reason",
        )
    assert exc.value.code == "VALIDATION_ERROR"
    with pytest.raises(BusinessError) as exc:
        service.review_action(
            item.id,
            ReviewActionCommand(AnnouncementAction.APPROVE, item.version - 1),
            manager,
            idempotency_key="stale",
        )
    assert exc.value.code == "VERSION_CONFLICT"
