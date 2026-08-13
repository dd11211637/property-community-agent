"""Deterministic announcement classification from user-visible content.

The category is an internal business attribute.  Users describe the subject and
facts; the application maps those facts to the closed domain enum so chat flows
never ask users to provide an implementation field such as ``category``.
"""

from property_agent.announcement.domain.enums import AnnouncementCategory

_CATEGORY_CUES: tuple[tuple[AnnouncementCategory, tuple[str, ...]], ...] = (
    (
        AnnouncementCategory.EMERGENCY,
        (
            "紧急疏散",
            "立即撤离",
            "人员被困",
            "明火",
            "火灾",
            "燃气泄漏",
            "爆炸",
        ),
    ),
    (
        AnnouncementCategory.SAFETY,
        (
            "消防",
            "安防",
            "安全检查",
            "安全隐患",
            "消防通道",
            "门禁",
            "防火",
            "防汛",
        ),
    ),
    (
        AnnouncementCategory.MAINTENANCE,
        (
            "停水",
            "停电",
            "停气",
            "供水",
            "供电",
            "维修",
            "检修",
            "维护",
            "抢修",
            "施工",
            "设施故障",
            "设备故障",
        ),
    ),
)


def classify_announcement_category(title: str, body: str) -> AnnouncementCategory:
    """Return the minimum stable category implied by title and body."""

    content = f"{title} {body}".strip()
    for category, cues in _CATEGORY_CUES:
        if any(cue in content for cue in cues):
            return category
    return AnnouncementCategory.GENERAL
