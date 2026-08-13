import pytest
from pydantic import ValidationError

from property_agent.announcement.adapters.api.schemas import CreateAnnouncementRequest
from property_agent.announcement.domain.classification import (
    classify_announcement_category,
)
from property_agent.announcement.domain.enums import AnnouncementCategory


def test_announcement_api_exposes_a_closed_category_contract():
    request = CreateAnnouncementRequest(
        title="消防检查通知",
        body="本周六开展消防设施检查。",
        category="SAFETY",
        audience_condition={},
    )
    assert request.category == AnnouncementCategory.SAFETY

    with pytest.raises(ValidationError):
        CreateAnnouncementRequest(
            title="消防检查通知",
            body="本周六开展消防设施检查。",
            category="使用这个稿件并保存草稿",
            audience_condition={},
        )


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("停水通知", "因供水设施维修将暂停供水。", AnnouncementCategory.MAINTENANCE),
        ("消防检查", "请勿占用消防通道。", AnnouncementCategory.SAFETY),
        ("紧急通知", "发现燃气泄漏，请立即撤离。", AnnouncementCategory.EMERGENCY),
        ("社区活动", "欢迎居民参加周末活动。", AnnouncementCategory.GENERAL),
    ],
)
def test_announcement_category_is_derived_from_visible_content(title, body, expected):
    assert classify_announcement_category(title, body) == expected
