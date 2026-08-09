from datetime import UTC, datetime
from uuid import uuid4

import pytest

from property_agent.announcement.domain.entities import Announcement
from property_agent.announcement.domain.enums import AnnouncementAction, AnnouncementStatus
from property_agent.platform.errors import BusinessError


def _draft() -> Announcement:
    now = datetime.now(UTC)
    return Announcement(
        uuid4(),
        uuid4(),
        "AN-1",
        "停水通知",
        "正文",
        "GENERAL",
        {},
        uuid4(),
        "key",
        created_at=now,
        updated_at=now,
    )


def test_state_machine_rejects_direct_publish() -> None:
    with pytest.raises(BusinessError) as exc:
        _draft().transition(AnnouncementAction.PUBLISH)
    assert exc.value.code == "INVALID_TRANSITION"


def test_rejected_announcement_returns_to_reviewable_draft_flow() -> None:
    item = _draft()
    item.transition(AnnouncementAction.SUBMIT_REVIEW)
    item.transition(AnnouncementAction.REJECT)
    assert item.status == AnnouncementStatus.REJECTED
    item.transition(AnnouncementAction.SUBMIT_REVIEW)
    assert item.status == AnnouncementStatus.PENDING_REVIEW


def test_published_body_cannot_be_edited() -> None:
    item = _draft()
    for action in (
        AnnouncementAction.SUBMIT_REVIEW,
        AnnouncementAction.APPROVE,
        AnnouncementAction.PUBLISH,
    ):
        item.transition(action)
    with pytest.raises(BusinessError):
        item.edit(
            title="新标题",
            body="新正文",
            category="GENERAL",
            audience_condition={},
            now=datetime.now(UTC),
        )
