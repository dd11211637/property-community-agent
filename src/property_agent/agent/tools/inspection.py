"""巡检与安防工具 — 只调用 ``InspectionTaskService`` / ``SecurityEventService``
公开方法（PRD §6.4 / §6.5.7）。

- ``inspection_list``：只读（任务 / 事件）
- ``inspection_create``：写-低风险，创建巡检任务
- ``inspection_submit_record``：写-低风险，补记录（补交必须带原因与实际时间）
- ``inspection_ai_suggest``：写-低风险，AI 异常建议**只入待人工确认区**，
  不直接生成安防事件
- ``close_high_risk_event``：写-高风险，工具永不执行，只转授权人工
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from property_agent.agent.policies import OperationLevel
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import (
    ContextProvider,
    Tool,
    assert_level,
    handover,
    idempotency_key,
    ok,
    require_confirmation,
    require_slot,
)
from property_agent.inspection.application.commands import (
    AddAiSuggestionCommand,
    CreateInspectionTaskCommand,
    ExecuteTaskActionCommand,
    InspectionTaskSearch,
    SecurityEventSearch,
)
from property_agent.inspection.domain.enums import TaskAction, TaskRecordType


def _task_brief(task: Any) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "business_no": getattr(task, "business_no", None),
        "title": getattr(task, "title", None),
        "status": str(getattr(task, "status", "")),
        "version": getattr(task, "version", None),
        "ai_pending_confirm": getattr(task, "ai_pending_confirm", False),
    }


def _event_brief(event: Any) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "business_no": getattr(event, "business_no", None),
        "event_type": str(getattr(event, "event_type", "")),
        "risk_level": str(getattr(event, "risk_level", "")),
        "status": str(getattr(event, "status", "")),
        "report_source": str(getattr(event, "report_source", "")),
    }


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def build_inspection_tools(
    task_service: Any,
    event_service: Any,
    context_provider: ContextProvider,
) -> dict[str, Tool]:
    def inspection_list(state: GraphState) -> dict[str, Any]:
        assert_level("inspection_list", OperationLevel.READ)
        context = context_provider(state)
        target = str(state.slots.get("target") or "task").lower()
        limit = int(state.slots.get("limit") or 20)
        if target == "event":
            search = SecurityEventSearch(
                statuses=tuple(state.slots.get("statuses") or ()),
                risk_levels=tuple(state.slots.get("risk_levels") or ()),
                limit=limit,
            )
            events = event_service.search_events(search, context)
            return ok(
                "inspection_list",
                target="event",
                count=len(events),
                items=[_event_brief(e) for e in events],
            )
        search_tasks = InspectionTaskSearch(
            statuses=tuple(state.slots.get("statuses") or ()),
            assigned_to_me=bool(state.slots.get("assigned_to_me")),
            limit=limit,
        )
        tasks = task_service.search_tasks(search_tasks, context)
        return ok(
            "inspection_list",
            target="task",
            count=len(tasks),
            items=[_task_brief(t) for t in tasks],
        )

    def inspection_create(state: GraphState) -> dict[str, Any]:
        assert_level("inspection_create", OperationLevel.WRITE_LOW_RISK)
        require_confirmation(state, "inspection_create")
        context = context_provider(state)
        route_points = tuple(state.slots.get("route_points") or ())
        if not route_points:
            route_points = (str(require_slot(state, "point", "inspection_create")),)
        command = CreateInspectionTaskCommand(
            title=str(require_slot(state, "title", "inspection_create")),
            description=str(require_slot(state, "description", "inspection_create")),
            route_points=route_points,
            planned_at=_as_datetime(state.slots.get("planned_at")),
            due_at=_as_datetime(state.slots.get("due_at")),
        )
        key = idempotency_key(
            state,
            "inspection_create",
            {
                "title": command.title,
                "description": command.description,
                "route_points": command.route_points,
                "planned_at": command.planned_at,
                "due_at": command.due_at,
            },
        )
        task = task_service.create_task(command, context, idempotency_key=key)
        return ok("inspection_create", task=_task_brief(task), idempotency_key=key)

    def inspection_submit_record(state: GraphState) -> dict[str, Any]:
        assert_level("inspection_submit_record", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "inspection_submit_record")
        context = context_provider(state)
        task_id = UUID(str(require_slot(state, "task_id", "inspection_submit_record")))
        is_supplement = bool(state.slots.get("is_supplement"))
        command = ExecuteTaskActionCommand(
            action=TaskAction.ADD_RECORD,
            expected_version=int(
                require_slot(state, "expected_version", "inspection_submit_record")
            ),
            note=state.slots.get("note"),
            record_type=TaskRecordType.SUPPLEMENT if is_supplement else TaskRecordType.POINT_RECORD,
            point=str(require_slot(state, "point", "inspection_submit_record")),
            is_supplement=is_supplement,
            actual_time=_as_datetime(state.slots.get("actual_time")),
            supplement_reason=state.slots.get("supplement_reason"),
            confirmation_token=token,
        )
        key = idempotency_key(
            state,
            "inspection_submit_record",
            {
                "task_id": task_id,
                "point": command.point,
                "note": command.note,
                "is_supplement": command.is_supplement,
                "expected_version": command.expected_version,
            },
        )
        task = task_service.execute_task_action(task_id, command, context, idempotency_key=key)
        return ok("inspection_submit_record", task=_task_brief(task), idempotency_key=key)

    def inspection_ai_suggest(state: GraphState) -> dict[str, Any]:
        assert_level("inspection_ai_suggest", OperationLevel.WRITE_LOW_RISK)
        require_confirmation(state, "inspection_ai_suggest")
        context = context_provider(state)
        task_id = UUID(str(require_slot(state, "task_id", "inspection_ai_suggest")))
        command = AddAiSuggestionCommand(
            point=str(require_slot(state, "point", "inspection_ai_suggest")),
            finding=str(require_slot(state, "finding", "inspection_ai_suggest")),
            severity=str(state.slots.get("severity") or "MEDIUM"),
            model=str(state.slots.get("model") or "inspection-ai"),
        )
        key = idempotency_key(
            state,
            "inspection_ai_suggest",
            {
                "task_id": task_id,
                "point": command.point,
                "finding": command.finding,
                "severity": command.severity,
            },
        )
        task = task_service.add_ai_suggestion(task_id, command, context, idempotency_key=key)
        return ok(
            "inspection_ai_suggest",
            task=_task_brief(task),
            pending_confirm=True,
            idempotency_key=key,
        )

    def close_high_risk_event(state: GraphState) -> dict[str, Any]:
        """高风险：智能体不得关闭安防事件，只转授权人工（PRD §6.4 / R-04）。"""
        assert_level("close_high_risk_event", OperationLevel.WRITE_HIGH_RISK)
        return handover(
            "close_high_risk_event",
            "高风险安防事件的等级确认与关闭需授权管理人员在业务端完成。",
            event_id=state.slots.get("event_id"),
            risk_level=state.slots.get("risk_level"),
        )

    return {
        "inspection_list": inspection_list,
        "inspection_create": inspection_create,
        "inspection_submit_record": inspection_submit_record,
        "inspection_ai_suggest": inspection_ai_suggest,
        "close_high_risk_event": close_high_risk_event,
    }
