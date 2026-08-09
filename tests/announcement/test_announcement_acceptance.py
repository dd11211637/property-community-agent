from uuid import uuid4

from property_agent.announcement.application.commands import (
    CreateAnnouncementCommand,
    ReviewActionCommand,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.enums import AnnouncementAction, AnnouncementStatus
from property_agent.platform.context import RequestContext
from property_agent.platform.roles import Role
from tests.announcement.support import Harness


def test_f03_customer_service_to_two_buildings_manager_confirms_publication() -> None:
    members = (uuid4(), uuid4(), uuid4())
    harness = Harness(audience_members=members)
    service = AnnouncementService(harness.uow)
    community = uuid4()
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "f03-cs")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "f03-manager")
    draft = service.create_draft(
        CreateAnnouncementCommand(
            "停水通知",
            "B1、B2 明日 09:00-12:00 停水",
            "MAINTENANCE",
            {"building_ids": ["B1", "B2"]},
        ),
        customer,
        idempotency_key="f03-create",
    )
    pending = service.submit_review(
        draft.id,
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, draft.version),
        customer,
        idempotency_key="f03-submit",
    )
    approved = service.review_action(
        pending.id,
        ReviewActionCommand(AnnouncementAction.APPROVE, pending.version),
        manager,
        idempotency_key="f03-approve",
    )
    published = service.publish(
        approved.id,
        ReviewActionCommand(
            AnnouncementAction.PUBLISH, approved.version, confirmation_token="confirmed"
        ),
        manager,
        idempotency_key="f03-publish",
    )
    assert published.status == AnnouncementStatus.PUBLISHED
    assert len(harness.messages.items) == len(members)
    assert len(harness.state.versions[published.id]) == 1
    assert len(harness.state.snapshots[published.id]) == 2
    assert {event["action"] for event in harness.state.audits} >= {
        "CREATE",
        "SUBMIT_REVIEW",
        "APPROVE",
        "PUBLISH",
    }


def test_r03_failed_delivery_remains_visible_retryable_and_does_not_republish() -> None:
    harness = Harness(audience_members=(uuid4(),))
    service = AnnouncementService(harness.uow)
    community = uuid4()
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "r03-manager")
    item = service.create_draft(
        CreateAnnouncementCommand("紧急通知", "请勿靠近施工区", "SAFETY", {"building_ids": ["B1"]}),
        manager,
        idempotency_key="r03-create",
    )
    item = service.submit_review(
        item.id,
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, item.version),
        manager,
        idempotency_key="r03-submit",
    )
    item = service.review_action(
        item.id,
        ReviewActionCommand(AnnouncementAction.APPROVE, item.version),
        manager,
        idempotency_key="r03-approve",
    )
    item = service.publish(
        item.id,
        ReviewActionCommand(
            AnnouncementAction.PUBLISH, item.version, confirmation_token="confirmed"
        ),
        manager,
        idempotency_key="r03-publish",
    )
    harness.messages.fail_delivery(0, "temporary provider outage")
    assert item.status == AnnouncementStatus.PUBLISHED
    assert harness.messages.items[0]["delivery_status"] == "FAILED"
    assert harness.messages.items[0]["retry_count"] == 1
    replay = service.publish(
        item.id,
        ReviewActionCommand(
            AnnouncementAction.PUBLISH, item.version - 1, confirmation_token="confirmed"
        ),
        manager,
        idempotency_key="r03-publish",
    )
    assert replay.id == item.id
    assert len(harness.messages.items) == 1
