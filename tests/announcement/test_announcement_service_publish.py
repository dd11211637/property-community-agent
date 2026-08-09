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


def _approved(service, customer, manager):
    item = service.create_draft(
        CreateAnnouncementCommand("停水", "明日停水", "GENERAL", {"building_ids": ["B1"]}),
        customer,
        idempotency_key="create",
    )
    item = service.submit_review(
        item.id,
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, item.version),
        customer,
        idempotency_key="submit",
    )
    return service.review_action(
        item.id,
        ReviewActionCommand(AnnouncementAction.APPROVE, item.version),
        manager,
        idempotency_key="approve",
    )


def test_confirmed_publish_uses_frozen_audience_and_is_idempotent() -> None:
    members = (uuid4(), uuid4())
    harness = Harness(audience_members=members)
    service = AnnouncementService(harness.uow)
    community = uuid4()
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "customer")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "manager")
    item = _approved(service, customer, manager)
    command = ReviewActionCommand(
        AnnouncementAction.PUBLISH, item.version, confirmation_token="confirmed"
    )
    published = service.publish(item.id, command, manager, idempotency_key="publish")
    replay = service.publish(item.id, command, manager, idempotency_key="publish")
    assert published.status == AnnouncementStatus.PUBLISHED
    assert replay.id == published.id
    assert len(harness.messages.items) == 2
    assert {message["receiver_id"] for message in harness.messages.items} == set(members)
    assert len(harness.state.snapshots[item.id]) == 2


def test_publish_requires_manager_valid_confirmation_and_approval() -> None:
    harness = Harness(audience_members=(uuid4(),))
    service = AnnouncementService(harness.uow)
    community = uuid4()
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "customer")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "manager")
    item = service.create_draft(
        CreateAnnouncementCommand("停水", "明日停水", "GENERAL", {"building_ids": ["B1"]}),
        customer,
        idempotency_key="create",
    )
    with pytest.raises(BusinessError) as exc:
        service.publish(
            item.id,
            ReviewActionCommand(
                AnnouncementAction.PUBLISH, item.version, confirmation_token="confirmed"
            ),
            customer,
            idempotency_key="forbidden",
        )
    assert exc.value.code == "FORBIDDEN"
    with pytest.raises(BusinessError) as exc:
        service.publish(
            item.id,
            ReviewActionCommand(AnnouncementAction.PUBLISH, item.version),
            manager,
            idempotency_key="missing-token",
        )
    assert exc.value.code == "CONFIRMATION_REQUIRED"


def test_withdrawal_reason_and_message_delivery_failure_are_separate() -> None:
    harness = Harness(audience_members=(uuid4(),))
    service = AnnouncementService(harness.uow)
    community = uuid4()
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "customer")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "manager")
    item = _approved(service, customer, manager)
    item = service.publish(
        item.id,
        ReviewActionCommand(
            AnnouncementAction.PUBLISH, item.version, confirmation_token="confirmed"
        ),
        manager,
        idempotency_key="publish",
    )
    harness.messages.fail_delivery(0, "simulated gateway failure")
    assert item.status == AnnouncementStatus.PUBLISHED
    assert harness.messages.items[0]["delivery_status"] == "FAILED"
    with pytest.raises(BusinessError) as exc:
        service.withdraw(
            item.id,
            ReviewActionCommand(AnnouncementAction.WITHDRAW, item.version),
            manager,
            idempotency_key="no-reason",
        )
    assert exc.value.code == "VALIDATION_ERROR"
    withdrawn = service.withdraw(
        item.id,
        ReviewActionCommand(AnnouncementAction.WITHDRAW, item.version, "内容有误"),
        manager,
        idempotency_key="withdraw",
    )
    assert withdrawn.status == AnnouncementStatus.WITHDRAWN
    assert harness.state.withdrawals[-1].reason == "内容有误"
