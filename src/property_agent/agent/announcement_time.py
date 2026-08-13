"""Trusted temporal normalization for announcement conversations.

Relative business dates are resolved by the application clock, never by the model.
The model may improve wording, while this module owns the concrete date rendered in
the final draft and the ISO timestamp used by scheduled publication.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def trusted_business_date(value: object, *, fallback: date | None = None) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return fallback or datetime.now(BUSINESS_TIMEZONE).date()


def resolve_announcement_time_slots(text: str, business_date: date) -> dict[str, Any]:
    """Resolve relative event dates and explicit publication times."""

    compact = str(text or "")
    slots: dict[str, Any] = {}
    if any(marker in compact for marker in ("今天", "今日")):
        slots["target_date"] = business_date.isoformat()
    elif any(marker in compact for marker in ("明天", "明日")):
        slots["target_date"] = (business_date + timedelta(days=1)).isoformat()
    elif any(marker in compact for marker in ("后天", "后日")):
        slots["target_date"] = (business_date + timedelta(days=2)).isoformat()

    publication = re.search(
        r"(?P<day>今晚|今天晚上|明晚|明天晚上|后天晚上)?\s*"
        r"(?P<period>凌晨|早上|上午|中午|下午|晚上)?\s*"
        r"(?P<hour>2[0-3]|1\d|0?\d)\s*(?:点|时)"
        r"(?:(?P<minute>[0-5]?\d)\s*分?)?\s*(?:发布|发送|推送)",
        compact,
    )
    if publication:
        day_text = publication.group("day") or ""
        offset = 2 if "后天" in day_text else 1 if "明" in day_text else 0
        hour = int(publication.group("hour"))
        minute = int(publication.group("minute") or 0)
        period = publication.group("period") or day_text
        if any(marker in period for marker in ("下午", "晚上", "晚")) and hour < 12:
            hour += 12
        elif "中午" in period and 1 <= hour < 11:
            hour += 12
        elif "凌晨" in period and hour == 12:
            hour = 0
        scheduled = datetime.combine(
            business_date + timedelta(days=offset),
            time(hour=hour, minute=minute),
            tzinfo=BUSINESS_TIMEZONE,
        )
        slots["scheduled_at"] = scheduled.isoformat()
    return slots


def concrete_date_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        resolved = date.fromisoformat(value)
    except ValueError:
        return None
    return f"{resolved.year}年{resolved.month}月{resolved.day}日"


def temporal_writing_guidance(*, target_date: object, scheduled_at: object) -> str:
    """Build server-authored facts for the copy model without granting it time authority."""

    facts: list[str] = []
    concrete = concrete_date_text(target_date)
    if concrete:
        facts.append(f"事项日期为{concrete}，正文必须使用这个绝对日期")
    if isinstance(scheduled_at, str):
        try:
            scheduled = datetime.fromisoformat(scheduled_at).astimezone(BUSINESS_TIMEZONE)
        except ValueError:
            scheduled = None
        if scheduled is not None:
            facts.append(
                "公告计划发布时间为"
                f"{scheduled.year}年{scheduled.month}月{scheduled.day}日"
                f"{scheduled.hour:02d}:{scheduled.minute:02d}；"
                "该时间仅用于系统调度，不写入公告正文或署名"
            )
    return "；".join(facts)


def materialize_relative_dates(text: str, *, target_date: object) -> str:
    """Replace relative event-day wording with the trusted absolute date."""

    concrete = concrete_date_text(target_date)
    if not concrete:
        return text
    return re.sub(r"明天|明日", concrete, str(text))
