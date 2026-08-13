"""Deterministic mapping from resident-described symptoms to repair categories."""

from __future__ import annotations

from property_agent.repair.domain.enums import RepairCategory

_CATEGORY_CUES: tuple[tuple[RepairCategory, tuple[str, ...]], ...] = (
    (RepairCategory.ELEVATOR, ("电梯", "困梯", "轿厢", "梯门", "电梯门")),
    (
        RepairCategory.ELECTRICAL,
        (
            "漏电",
            "电路",
            "电线",
            "插座",
            "停电",
            "跳闸",
            "电灯",
            "灯具",
            "照明",
            "灯泡",
            "开关",
            "配电箱",
        ),
    ),
    (
        RepairCategory.WATER_PLUMBING,
        (
            "漏水",
            "渗水",
            "水管",
            "下水",
            "排水",
            "水龙头",
            "马桶",
            "地漏",
            "堵塞",
            "返水",
        ),
    ),
)


def classify_repair_category(description: str) -> RepairCategory:
    """Classify a symptom without requiring users to know internal enums."""

    text = (description or "").strip().lower()
    return next(
        (category for category, cues in _CATEGORY_CUES if any(cue.lower() in text for cue in cues)),
        RepairCategory.OTHER,
    )
