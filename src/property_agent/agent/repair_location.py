"""Deterministic extraction of user-authored repair locations."""

from __future__ import annotations

import re

_LOCATION_ALIASES: tuple[tuple[str, str], ...] = (
    ("地下停车场", "地下车库"),
    ("地下车库", "地下车库"),
    ("公共走廊", "公共走廊"),
    ("电梯前室", "电梯厅"),
    ("生活阳台", "生活阳台"),
    ("入户玄关", "玄关"),
    ("入户门口", "入户门口"),
    ("主卧室", "主卧"),
    ("次卧室", "次卧"),
    ("儿童房", "儿童房"),
    ("老人房", "老人房"),
    ("衣帽间", "衣帽间"),
    ("储物间", "储物间"),
    ("洗手间", "卫生间"),
    ("卫生间", "卫生间"),
    ("浴室", "卫生间"),
    ("厕所", "卫生间"),
    ("电梯厅", "电梯厅"),
    ("走廊", "走廊"),
    ("楼道", "楼道"),
    ("车库", "车库"),
    ("主卧", "主卧"),
    ("次卧", "次卧"),
    ("卧室", "卧室"),
    ("书房", "书房"),
    ("客厅", "客厅"),
    ("餐厅", "餐厅"),
    ("厨房", "厨房"),
    ("阳台", "阳台"),
    ("玄关", "玄关"),
)

_LANDMARK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:水槽|马桶|洗衣机|冰箱|灶台|洗手台)(?:的)?(?:下方|下面|旁边|后面|附近)"),
    re.compile(r"(?:床头|窗边|窗户旁|电视墙|吊顶|天花板|入户门口)"),
)

_BUILDING_AREA = re.compile(
    r"(?P<building>\d+栋(?:\d+单元)?)(?P<area>楼道|走廊|电梯厅|入口|大厅|地下车库)?"
)


def extract_repair_location(text: str, *, prefer_last: bool = False) -> str | None:
    """Return a normalized location only when it is explicitly present in user text."""

    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return None
    building = _BUILDING_AREA.search(compact)
    if building and building.group("area"):
        return f"{building.group('building')}{building.group('area')}"
    matches: list[tuple[int, int, str]] = []
    for alias, canonical in sorted(_LOCATION_ALIASES, key=lambda item: len(item[0]), reverse=True):
        for match in re.finditer(re.escape(alias), compact):
            if any(match.start() < end and match.end() > start for start, end, _ in matches):
                continue
            matches.append((match.start(), match.end(), canonical))
    if prefer_last:
        room_match = (
            max(matches, key=lambda item: (item[0], item[1] - item[0])) if matches else None
        )
    else:
        room_match = (
            min(matches, key=lambda item: (item[0], item[0] - item[1])) if matches else None
        )
    landmark_match = next(
        (match for pattern in _LANDMARK_PATTERNS if (match := pattern.search(compact))),
        None,
    )
    if room_match and landmark_match:
        start, end, room = room_match
        landmark = landmark_match.group(0)
        if landmark_match.start() >= start and landmark_match.end() <= end:
            return room
        if 0 <= landmark_match.start() - end <= 1:
            return f"{room}{landmark}"
    if room_match:
        return room_match[2]
    return landmark_match.group(0) if landmark_match else None


__all__ = ["extract_repair_location"]
