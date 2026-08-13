from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
    ReviewActionCommand,
    ScheduleAnnouncementCommand,
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


def test_confirmed_schedule_is_persistent_and_publishes_when_due() -> None:
    members = (uuid4(), uuid4())
    harness = Harness(audience_members=members)
    service = AnnouncementService(harness.uow)
    community = uuid4()
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "customer")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "manager")
    item = _approved(service, customer, manager)
    scheduled_at = datetime.now(UTC) + timedelta(minutes=10)

    scheduled = service.schedule_publish(
        item.id,
        ScheduleAnnouncementCommand(item.version, scheduled_at, "confirmed"),
        manager,
        idempotency_key="schedule",
    )

    assert scheduled.status == AnnouncementStatus.APPROVED
    assert scheduled.scheduled_at == scheduled_at
    assert harness.messages.items == []
    assert service.publish_due(now=scheduled_at - timedelta(seconds=1)) == 0

    assert service.publish_due(now=scheduled_at) == 1
    assert harness.state.announcements[item.id].status == AnnouncementStatus.PUBLISHED
    assert {message["receiver_id"] for message in harness.messages.items} == set(members)
    assert harness.state.reviews[-1].action == AnnouncementAction.PUBLISH
    assert harness.state.reviews[-1].reason == "SCHEDULED_EXECUTION"

    assert service.publish_due(now=scheduled_at + timedelta(minutes=1)) == 0
    assert len(harness.messages.items) == len(members)


def test_schedule_rejects_past_time_and_non_manager() -> None:
    harness = Harness(audience_members=(uuid4(),))
    service = AnnouncementService(harness.uow)
    community = uuid4()
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "customer")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "manager")
    item = _approved(service, customer, manager)
    future = datetime.now(UTC) + timedelta(minutes=10)

    with pytest.raises(BusinessError) as forbidden:
        service.schedule_publish(
            item.id,
            ScheduleAnnouncementCommand(item.version, future, "confirmed"),
            customer,
            idempotency_key="schedule-forbidden",
        )
    assert forbidden.value.code == "FORBIDDEN"

    with pytest.raises(BusinessError) as invalid:
        service.schedule_publish(
            item.id,
            ScheduleAnnouncementCommand(
                item.version, datetime.now(UTC) - timedelta(seconds=1), "confirmed"
            ),
            manager,
            idempotency_key="schedule-past",
        )
    assert invalid.value.code == "VALIDATION_ERROR"


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
    withdraw_command = ReviewActionCommand(AnnouncementAction.WITHDRAW, item.version, "内容有误")
    withdrawn = service.withdraw(
        item.id,
        withdraw_command,
        manager,
        idempotency_key="withdraw",
    )
    assert withdrawn.status == AnnouncementStatus.WITHDRAWN
    assert harness.state.withdrawals[-1].reason == "内容有误"
    assert len(harness.messages.items) == 2
    assert harness.messages.items[-1]["event_type"] == "ANNOUNCEMENT_WITHDRAWN"
    assert harness.messages.items[-1]["receiver_id"] == harness.audiences.member_ids[0]

    replay = service.withdraw(
        item.id,
        withdraw_command,
        manager,
        idempotency_key="withdraw",
    )
    assert replay.id == withdrawn.id
    assert len(harness.messages.items) == 2


def test_resident_reads_only_published_announcements_in_frozen_audience() -> None:
    resident_id = uuid4()
    harness = Harness(audience_members=(resident_id,))
    service = AnnouncementService(harness.uow)
    community = uuid4()
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "customer")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "manager")
    resident = RequestContext(resident_id, community, frozenset({Role.RESIDENT}), "resident")
    other_resident = RequestContext(uuid4(), community, frozenset({Role.RESIDENT}), "other")

    approved = _approved(service, customer, manager)
    assert service.search(AnnouncementSearch((), 20, 0), resident) == []

    published = service.publish(
        approved.id,
        ReviewActionCommand(
            AnnouncementAction.PUBLISH,
            approved.version,
            confirmation_token="confirmed",
        ),
        manager,
        idempotency_key="publish",
    )

    assert service.get(published.id, resident).id == published.id
    assert [item.id for item in service.search(AnnouncementSearch((), 20, 0), resident)] == [
        published.id
    ]
    assert service.search(AnnouncementSearch((), 20, 0), other_resident) == []

    with pytest.raises(BusinessError) as exc:
        service.get(published.id, other_resident)
    assert exc.value.code == "RESOURCE_NOT_FOUND"

    with pytest.raises(BusinessError) as exc:
        service.versions(published.id, resident)
    assert exc.value.code == "FORBIDDEN"
