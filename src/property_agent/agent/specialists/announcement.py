"""Stateless Announcement specialist."""

from __future__ import annotations

import json

from property_agent.agent.orchestration import SpecialistName
from property_agent.agent.specialists.base import StatelessSpecialist


class AnnouncementSpecialist(StatelessSpecialist):
    name = SpecialistName.ANNOUNCEMENT
    domain = "announcement"

    def choose_capability(self, step, state, prior_results):
        del step, prior_results
        action = state.slots.get("action")
        return {
            "get": "announcement_get",
            "draft": "announcement_draft",
            "revise": "announcement_revise",
            "create": "announcement_create_draft",
            "publish": "announce_publish",
            "schedule": "announcement_schedule_publish",
        }.get(action, "announcement_list")

    def project_parameters(self, capability, step, state, prior_results):
        values = {**state.slots, **step.parameters}
        if capability == "announcement_draft" and prior_results:
            grounded = json.dumps(prior_results[-1].data, ensure_ascii=False, default=str)[:3000]
            values["requirements"] = (
                f"已核验事实：{grounded}\n用户要求：{values.get('requirements', '')}"
            )
        fields = {
            "announcement_list": ("statuses", "limit"),
            "announcement_get": ("announcement_id",),
            "community_knowledge_search": ("query", "limit"),
            "announcement_draft": ("topic", "audience", "requirements"),
            "announcement_revise": (
                "title",
                "body",
                "audience",
                "category",
                "revision_instruction",
            ),
            "announcement_create_draft": ("title", "body", "audience"),
            "announce_publish": ("announcement_id", "expected_version"),
            "announcement_schedule_publish": (
                "announcement_id",
                "expected_version",
                "scheduled_at",
            ),
        }[capability]
        projected = {key: values.get(key) for key in fields}
        if capability == "announcement_list":
            projected["statuses"] = tuple(projected.get("statuses") or ())
            projected["limit"] = int(projected.get("limit") or 20)
        if capability == "community_knowledge_search":
            projected["limit"] = int(projected.get("limit") or 10)
        return projected

    def success_message(self, capability, data):
        del data
        if capability in {"announcement_draft", "announcement_revise"}:
            return "已根据核验事实准备公告草稿。"
        if capability in {"announce_publish", "announcement_schedule_publish"}:
            return "公告发布操作已完成。"
        return "公告步骤已完成。"
