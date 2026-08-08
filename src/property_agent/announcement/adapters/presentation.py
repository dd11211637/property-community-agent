from typing import Any

from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.entities import (
    Announcement,
    AnnouncementVersion,
    AudienceSnapshot,
)
from property_agent.platform.context import RequestContext


def announcement_data(
    announcement: Announcement, service: AnnouncementService, context: RequestContext
) -> dict[str, Any]:
    return {
        "id": str(announcement.id),
        "business_no": announcement.business_no,
        "community_id": str(announcement.community_id),
        "title": announcement.title,
        "body": announcement.body,
        "category": announcement.category,
        "audience_condition": announcement.audience_condition,
        "scheduled_at": announcement.scheduled_at.isoformat()
        if announcement.scheduled_at
        else None,
        "status": announcement.status.value,
        "version": announcement.version,
        "manager_recheck_required": announcement.manager_recheck_required,
        "published_at": announcement.published_at.isoformat()
        if announcement.published_at
        else None,
        "withdrawn_at": announcement.withdrawn_at.isoformat()
        if announcement.withdrawn_at
        else None,
        "available_actions": [
            item.value for item in service.available_actions(announcement, context)
        ],
        "created_at": announcement.created_at.isoformat(),
        "updated_at": announcement.updated_at.isoformat(),
    }


def audience_snapshot_data(snapshot: AudienceSnapshot) -> dict[str, Any]:
    return {
        "condition": snapshot.condition,
        "count": snapshot.count,
        "samples": list(snapshot.samples),
        "generated_at": snapshot.generated_at.isoformat(),
    }


def version_data(version: AnnouncementVersion) -> dict[str, Any]:
    return {
        "version_no": version.version_no,
        "title": version.title,
        "body": version.body,
        "category": version.category,
        "audience_condition": version.audience_condition,
        "operator_id": str(version.operator_id),
        "source": version.source.value,
        "created_at": version.created_at.isoformat(),
    }
