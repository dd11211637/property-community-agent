from datetime import date

from property_agent.agent.announcement_time import (
    materialize_relative_dates,
    resolve_announcement_time_slots,
    temporal_writing_guidance,
)


def test_relative_event_date_and_publication_time_use_trusted_business_date():
    slots = resolve_announcement_time_slots("明天将会停水，今晚8点发布", date(2026, 8, 13))

    assert slots["target_date"] == "2026-08-14"
    assert slots["scheduled_at"] == "2026-08-13T20:00:00+08:00"


def test_relative_date_is_materialized_in_model_copy():
    assert (
        materialize_relative_dates("关于明日停水的通知：明天暂停供水。", target_date="2026-08-14")
        == "关于2026年8月14日停水的通知：2026年8月14日暂停供水。"
    )


def test_writing_guidance_separates_event_date_from_publish_schedule():
    guidance = temporal_writing_guidance(
        target_date="2026-08-14",
        scheduled_at="2026-08-13T20:00:00+08:00",
    )

    assert "事项日期为2026年8月14日" in guidance
    assert "公告计划发布时间为2026年8月13日20:00" in guidance
    assert "不写入公告正文或署名" in guidance
