"""Serialization helpers for inspection Agent tool responses."""

from typing import Any


def _task_brief(task: Any) -> dict[str, Any]:
    return {
        "entity_type": "INSPECTION_TASK",
        "id": str(task.id),
        "business_no": getattr(task, "business_no", None),
        "title": getattr(task, "title", None),
        "status": str(getattr(task, "status", "")),
        "version": getattr(task, "version", None),
        "route_points": list(getattr(task, "route_points", ()) or ()),
        "assignee_id": str(task.assignee_id) if getattr(task, "assignee_id", None) else None,
        "ai_pending_confirm": getattr(task, "ai_pending_confirm", False),
    }


def _event_brief(event: Any) -> dict[str, Any]:
    return {
        "entity_type": "SECURITY_EVENT",
        "id": str(event.id),
        "business_no": getattr(event, "business_no", None),
        "event_type": str(getattr(event, "event_type", "")),
        "risk_level": str(getattr(event, "risk_level", "")),
        "status": str(getattr(event, "status", "")),
        "location": getattr(event, "location", None),
        "description": getattr(event, "description", None),
        "assignee_id": str(event.assignee_id) if getattr(event, "assignee_id", None) else None,
        "report_source": str(getattr(event, "report_source", "")),
    }
