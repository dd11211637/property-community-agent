from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from property_agent.announcement.application.commands import (
    CreateAnnouncementCommand,
    EditAnnouncementCommand,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.enums import VersionSource
from property_agent.platform.context import ExecutionSource, RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.roles import Role
from tests.announcement.support import Harness


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def context() -> RequestContext:
    return RequestContext(uuid4(), uuid4(), frozenset({Role.CUSTOMER_SERVICE}), "announcement-test")


def _command(**changes) -> CreateAnnouncementCommand:
    base = {
        "title": "停水通知",
        "body": "明日检修",
        "category": "GENERAL",
        "audience_condition": {"building_ids": ["B1"]},
    }
    base.update(changes)
    return CreateAnnouncementCommand(**base)


def test_create_and_edit_draft_keep_immutable_versions(harness, context) -> None:
    service = AnnouncementService(harness.uow)
    item = service.create_draft(_command(), context, idempotency_key="create-1")
    edited = service.edit_draft(
        item.id,
        EditAnnouncementCommand(
            "新标题",
            "新正文",
            "SAFETY",
            {"building_ids": ["B2"]},
            item.version,
            VersionSource.AI_SUGGESTION_ADOPTED,
        ),
        context,
        idempotency_key="edit-1",
    )
    versions = service.versions(item.id, context)
    assert edited.version == 2
    assert [version.version_no for version in versions] == [1, 2]
    assert versions[0].title == "停水通知"
    assert versions[1].source == VersionSource.AI_SUGGESTION_ADOPTED
    assert edited.manager_recheck_required is True


def test_cross_community_is_not_discoverable(harness, context) -> None:
    service = AnnouncementService(harness.uow)
    item = service.create_draft(_command(), context, idempotency_key="create-2")
    stranger = RequestContext(uuid4(), uuid4(), frozenset({Role.MANAGER}), "stranger")
    with pytest.raises(BusinessError) as exc:
        service.get(item.id, stranger)
    assert exc.value.code == "RESOURCE_NOT_FOUND"


def test_draft_validates_roles_audience_and_idempotency(harness, context) -> None:
    service = AnnouncementService(harness.uow)
    with pytest.raises(BusinessError):
        service.create_draft(
            _command(audience_condition={"member_ids": ["x"]}), context, idempotency_key="bad"
        )
    item = service.create_draft(_command(), context, idempotency_key="same")
    assert service.create_draft(_command(), context, idempotency_key="same").id == item.id
    with pytest.raises(BusinessError) as exc:
        service.create_draft(_command(title="不同"), context, idempotency_key="same")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(BusinessError) as exc:
        service.create_draft(
            _command(scheduled_at=datetime(2026, 8, 23, tzinfo=UTC)),
            context,
            idempotency_key="same",
        )
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    resident = RequestContext(uuid4(), context.community_id, frozenset({Role.RESIDENT}), "resident")
    with pytest.raises(BusinessError) as exc:
        service.create_draft(_command(), resident, idempotency_key="resident")
    assert exc.value.code == "FORBIDDEN"


def test_agent_create_draft_consumes_confirmation_but_human_contract_is_unchanged(
    harness, context
) -> None:
    service = AnnouncementService(harness.uow)
    human = service.create_draft(_command(), context, idempotency_key="human-create")
    assert human.id is not None
    assert harness.confirmations.consumed == []

    agent = replace(context, execution_source=ExecutionSource.AGENT)
    with pytest.raises(BusinessError) as exc:
        service.create_draft(_command(), agent, idempotency_key="agent-unconfirmed")
    assert exc.value.code == "CONFIRMATION_REQUIRED"

    created = service.create_draft(
        _command(confirmation_token="confirmed", approval_ref="approval-create"),
        agent,
        idempotency_key="agent-confirmed",
    )
    assert created.id is not None
    assert harness.confirmations.consumed == ["confirmed"]
