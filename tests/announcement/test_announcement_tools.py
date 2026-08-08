from uuid import uuid4

from property_agent.announcement.adapters.tool_adapter import TOOL_SCHEMAS, AnnouncementToolAdapter
from property_agent.announcement.application.service import AnnouncementService
from property_agent.platform.context import RequestContext
from property_agent.platform.roles import Role
from tests.announcement.support import Harness


def test_agent_tools_only_expose_draft_safe_operations() -> None:
    assert not {"publish_announcement", "withdraw_announcement"}.intersection(TOOL_SCHEMAS)
    harness = Harness(audience_members=(uuid4(),))
    service = AnnouncementService(harness.uow)
    context = RequestContext(uuid4(), uuid4(), frozenset({Role.CUSTOMER_SERVICE}), "tool")
    adapter = AnnouncementToolAdapter(service)
    item = adapter.create_announcement_draft(
        {
            "title": "AI 草稿",
            "body": "请确认",
            "category": "GENERAL",
            "audience_condition": {"building_ids": ["B1"]},
            "idempotency_key": "create",
        },
        context,
    )
    assert item["status"] == "DRAFT"
    assert (
        adapter.preview_announcement_audience({"announcement_id": item["id"]}, context)["count"]
        == 1
    )
    submitted = adapter.submit_announcement_review(
        {
            "announcement_id": item["id"],
            "expected_version": item["version"],
            "idempotency_key": "submit",
        },
        context,
    )
    assert submitted["status"] == "PENDING_REVIEW"
