from typing import Any

from property_agent.inspection.application.commands import TimelineEntry
from property_agent.inspection.application.ports import RequestContext
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.entities import InspectionTask, SecurityEvent


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def task_data(
    task: InspectionTask, service: InspectionTaskService, context: RequestContext
) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "business_no": task.business_no,
        "community_id": str(task.community_id),
        "title": task.title,
        "description": task.description,
        "route_points": list(task.route_points),
        "status": task.status.value,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "planned_at": _iso(task.planned_at),
        "due_at": _iso(task.due_at),
        "version": task.version,
        "available_actions": [a.value for a in service.available_task_actions(task, context)],
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "closed_at": _iso(task.closed_at),
    }


def event_data(
    event: SecurityEvent, service: SecurityEventService, context: RequestContext
) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "business_no": event.business_no,
        "community_id": str(event.community_id),
        "source_task_id": str(event.source_task_id) if event.source_task_id else None,
        "reporter_id": str(event.reporter_id),
        "event_type": event.event_type.value,
        "risk_level": event.risk_level.value,
        "location": event.location,
        "description": event.description,
        "status": event.status.value,
        "assignee_id": str(event.assignee_id) if event.assignee_id else None,
        "grade_confirmed_by": str(event.grade_confirmed_by) if event.grade_confirmed_by else None,
        "version": event.version,
        "available_actions": [a.value for a in service.available_event_actions(event, context)],
        "created_at": _iso(event.created_at),
        "updated_at": _iso(event.updated_at),
        "closed_at": _iso(event.closed_at),
    }


def timeline_entry_data(entry: TimelineEntry) -> dict[str, Any]:
    return {
        "entry_type": entry.entry_type,
        "action": entry.action,
        "operator_id": str(entry.operator_id),
        "created_at": entry.created_at.isoformat(),
        "from_status": entry.from_status,
        "to_status": entry.to_status,
        "reason": entry.reason,
        "note": entry.note,
        "attachment_ids": [str(v) for v in entry.attachment_ids],
    }
