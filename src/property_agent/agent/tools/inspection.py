"""巡检与安防工具 — 只调用 ``InspectionTaskService`` / ``SecurityEventService``
公开方法（PRD §6.4 / §6.5.7）。

- ``inspection_list``：只读（任务 / 事件）
- ``inspection_create``：写-低风险，创建巡检任务
- ``inspection_submit_record``：写-低风险，补记录（补交必须带原因与实际时间）
- ``inspection_ai_suggest``：写-低风险，AI 异常建议**只入待人工确认区**，
  不直接生成安防事件
- ``close_high_risk_event``：写-高风险，工具永不执行，只转授权人工
"""

from dataclasses import asdict
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
from property_agent.agent.tools.inspection_presenters import _event_brief, _task_brief
from property_agent.inspection.application.commands import (
    AddAiSuggestionCommand,
    CreateInspectionTaskCommand,
    CreateSecurityEventCommand,
    ExecuteEventActionCommand,
    ExecuteTaskActionCommand,
    InspectionTaskSearch,
    SecurityEventSearch,
)
from property_agent.inspection.domain.classification import classify_security_event
from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventType,
    TaskAction,
    TaskRecordType,
)


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _apply_event_risk_floor(slots: dict[str, Any]) -> None:
    """Apply a deterministic minimum risk level; model/user input may only raise it."""
    description = str(slots.get("description") or "")
    event_type, minimum_risk = classify_security_event(description)
    slots["event_type"] = event_type.value
    requested = str(slots.get("risk_level") or "").upper()
    if minimum_risk == EventRiskLevel.HIGH_RISK:
        slots["risk_level"] = "HIGH_RISK"
        slots["safety_notice"] = (
            "请优先远离危险区域，不要触碰可疑设备或明火；如存在即时人身危险，"
            "请立即联系当地紧急救援。确认上报后系统会同步通知值班人员。"
        )
    elif requested == "HIGH_RISK":
        slots["risk_level"] = "HIGH_RISK"
    else:
        slots["risk_level"] = minimum_risk.value


class InspectionToolSet:
    """Bound inspection and security-event tools with stable public tool names."""

    def __init__(
        self, task_service: Any, event_service: Any, context_provider: ContextProvider
    ) -> None:
        self._task_service = task_service
        self._event_service = event_service
        self._context_provider = context_provider

    def prepare_inspection(self, state: GraphState) -> GraphState:
        """Resolve user-facing task/event references from authorized business data."""
        action = str(state.slots.get("action") or "").lower()
        context = self._context_provider(state)
        if action in {"report_event", "create_event", "event_create"}:
            _apply_event_risk_floor(state.slots)
        task_statuses = {
            "start": ("ASSIGNED",),
            "start_task": ("ASSIGNED",),
            "record": ("IN_PROGRESS", "SUBMITTED"),
            "add_record": ("IN_PROGRESS", "SUBMITTED"),
            "supplement": ("IN_PROGRESS", "SUBMITTED"),
            "submit_record": ("IN_PROGRESS",),
            "submit_records": ("IN_PROGRESS",),
            "complete_records": ("IN_PROGRESS",),
        }
        if action in task_statuses:
            task_id = state.slots.get("task_id")
            if task_id:
                task = self._task_service.get_task(UUID(str(task_id)), context)
                state.slots["task_id"] = str(task.id)
                state.slots["expected_version"] = task.version
                state.slots["selected_task"] = _task_brief(task)
                state.slots.pop("_selection_options", None)
                return state
            tasks = self._task_service.search_tasks(
                InspectionTaskSearch(statuses=task_statuses[action], assigned_to_me=True, limit=20),
                context,
            )
            if len(tasks) == 1:
                task = tasks[0]
                state.slots["task_id"] = str(task.id)
                state.slots["expected_version"] = task.version
                state.slots["selected_task"] = _task_brief(task)
            elif len(tasks) > 1:
                state.slots["_selection_options"] = {
                    "field": "task_id",
                    "label": "巡检任务",
                    "prompt": "请选择要操作的巡检任务",
                    "options": [
                        {"label": f"{task.title}（{task.business_no}）", "value": str(task.id)}
                        for task in tasks
                    ],
                }
        if action in {"dispose_event", "submit_disposal"}:
            event_id = state.slots.get("event_id")
            if event_id:
                event = self._event_service.get_event(UUID(str(event_id)), context)
                state.slots["event_id"] = str(event.id)
                state.slots["expected_version"] = event.version
                state.slots["selected_event"] = _event_brief(event)
                state.slots.pop("_selection_options", None)
                return state
            events = self._event_service.search_events(
                SecurityEventSearch(statuses=("ASSIGNED",), assigned_to_me=True, limit=20),
                context,
            )
            if len(events) == 1:
                event = events[0]
                state.slots["event_id"] = str(event.id)
                state.slots["expected_version"] = event.version
                state.slots["selected_event"] = _event_brief(event)
            elif len(events) > 1:
                state.slots["_selection_options"] = {
                    "field": "event_id",
                    "label": "安防事件",
                    "prompt": "请选择要提交处置结果的安防事件",
                    "options": [
                        {
                            "label": f"{event.location}（{event.business_no}）",
                            "value": str(event.id),
                        }
                        for event in events
                    ],
                }
        return state

    def inspection_list(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_list", OperationLevel.READ)
        context = self._context_provider(state)
        target = str(state.slots.get("target") or "task").lower()
        limit = int(state.slots.get("limit") or 20)
        if target == "event":
            search = SecurityEventSearch(
                statuses=tuple(state.slots.get("statuses") or ()),
                risk_levels=tuple(state.slots.get("risk_levels") or ()),
                limit=limit,
            )
            events = self._event_service.search_events(search, context)
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
        tasks = self._task_service.search_tasks(search_tasks, context)
        summary = self._task_service.summarize_tasks(search_tasks, context)
        return ok(
            "inspection_list",
            target="task",
            count=len(tasks),
            items=[_task_brief(t) for t in tasks],
            **summary,
        )

    def inspection_get_task(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_list", OperationLevel.READ)
        context = self._context_provider(state)
        task_id = UUID(str(require_slot(state, "task_id", "inspection_get_task")))
        task = self._task_service.get_task(task_id, context)
        return ok(
            "inspection_get_task",
            task={
                **_task_brief(task),
                "available_actions": [
                    action.value
                    for action in self._task_service.available_task_actions(task, context)
                ],
            },
            timeline=[
                asdict(entry) for entry in self._task_service.task_timeline(task.id, context)
            ],
        )

    def inspection_get_event(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_list", OperationLevel.READ)
        context = self._context_provider(state)
        event_id = UUID(str(require_slot(state, "event_id", "inspection_get_event")))
        event = self._event_service.get_event(event_id, context)
        return ok(
            "inspection_get_event",
            event={
                **_event_brief(event),
                "available_actions": [
                    action.value
                    for action in self._event_service.available_event_actions(event, context)
                ],
            },
            timeline=[
                asdict(entry) for entry in self._event_service.event_timeline(event.id, context)
            ],
        )

    def inspection_create(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_create", OperationLevel.WRITE_LOW_RISK)
        require_confirmation(state, "inspection_create")
        context = self._context_provider(state)
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
        task = self._task_service.create_task(command, context, idempotency_key=key)
        return ok("inspection_create", task=_task_brief(task), idempotency_key=key)

    def inspection_start_task(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_start_task", OperationLevel.WRITE_LOW_RISK)
        require_confirmation(state, "inspection_start_task")
        context = self._context_provider(state)
        task_id = UUID(str(require_slot(state, "task_id", "inspection_start_task")))
        expected_version = int(require_slot(state, "expected_version", "inspection_start_task"))
        key = idempotency_key(
            state,
            "inspection_start_task",
            {"task_id": task_id, "expected_version": expected_version},
        )
        task = self._task_service.execute_task_action(
            task_id,
            ExecuteTaskActionCommand(action=TaskAction.START, expected_version=expected_version),
            context,
            idempotency_key=key,
        )
        return ok("inspection_start_task", task=_task_brief(task), idempotency_key=key)

    def _record(self, state: GraphState, *, final: bool) -> dict[str, Any]:
        tool = "inspection_submit_records" if final else "inspection_add_record"
        assert_level(tool, OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, tool)
        context = self._context_provider(state)
        task_id = UUID(str(require_slot(state, "task_id", tool)))
        expected_version = int(require_slot(state, "expected_version", tool))
        record_type = TaskRecordType(
            str(state.slots.get("record_type") or ("COMPLETION" if final else "POINT_RECORD"))
        )
        command = ExecuteTaskActionCommand(
            action=TaskAction.SUBMIT_RECORDS if final else TaskAction.ADD_RECORD,
            expected_version=expected_version,
            note=str(require_slot(state, "note", tool)),
            record_type=record_type,
            point=str(require_slot(state, "point", tool)),
            confirmation_token=token if final else None,
        )
        key = idempotency_key(
            state,
            tool,
            {
                "task_id": task_id,
                "expected_version": expected_version,
                "point": command.point,
                "note": command.note,
                "record_type": record_type.value,
            },
        )
        task = self._task_service.execute_task_action(
            task_id, command, context, idempotency_key=key
        )
        return ok(tool, task=_task_brief(task), idempotency_key=key)

    def inspection_add_record(self, state: GraphState) -> dict[str, Any]:
        return self._record(state, final=False)

    def inspection_submit_records(self, state: GraphState) -> dict[str, Any]:
        return self._record(state, final=True)

    def security_event_create(self, state: GraphState) -> dict[str, Any]:
        assert_level("security_event_create", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "security_event_create")
        context = self._context_provider(state)
        _apply_event_risk_floor(state.slots)
        event_type = EventType(str(state.slots["event_type"]))
        risk_level = EventRiskLevel(str(state.slots["risk_level"]))
        command = CreateSecurityEventCommand(
            source_task_id=UUID(str(state.slots["task_id"]))
            if state.slots.get("task_id")
            else None,
            event_type=event_type,
            risk_level=risk_level,
            location=str(require_slot(state, "location", "security_event_create")),
            description=str(require_slot(state, "description", "security_event_create")),
            confirmation_token=token,
            report_source="AI",
        )
        key = idempotency_key(
            state,
            "security_event_create",
            {
                "event_type": event_type.value,
                "risk_level": risk_level.value,
                "location": command.location,
                "description": command.description,
            },
        )
        event = self._event_service.create_event(command, context, idempotency_key=key)
        if event.risk_level == EventRiskLevel.HIGH_RISK:
            state.handover_required = True
        return ok(
            "security_event_create",
            event=_event_brief(event),
            handover_required=state.handover_required,
            idempotency_key=key,
        )

    def security_event_submit_disposal(self, state: GraphState) -> dict[str, Any]:
        assert_level("security_event_submit_disposal", OperationLevel.WRITE_LOW_RISK)
        require_confirmation(state, "security_event_submit_disposal")
        context = self._context_provider(state)
        event_id = UUID(str(require_slot(state, "event_id", "security_event_submit_disposal")))
        expected_version = int(
            require_slot(state, "expected_version", "security_event_submit_disposal")
        )
        note = str(require_slot(state, "note", "security_event_submit_disposal"))
        key = idempotency_key(
            state,
            "security_event_submit_disposal",
            {"event_id": event_id, "expected_version": expected_version, "note": note},
        )
        event = self._event_service.execute_event_action(
            event_id,
            ExecuteEventActionCommand(
                action=EventAction.SUBMIT_DISPOSAL, expected_version=expected_version, note=note
            ),
            context,
            idempotency_key=key,
        )
        return ok("security_event_submit_disposal", event=_event_brief(event), idempotency_key=key)

    def inspection_submit_record(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_submit_record", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "inspection_submit_record")
        context = self._context_provider(state)
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
        task = self._task_service.execute_task_action(
            task_id, command, context, idempotency_key=key
        )
        return ok("inspection_submit_record", task=_task_brief(task), idempotency_key=key)

    def inspection_ai_suggest(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_ai_suggest", OperationLevel.WRITE_LOW_RISK)
        require_confirmation(state, "inspection_ai_suggest")
        context = self._context_provider(state)
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
        task = self._task_service.add_ai_suggestion(task_id, command, context, idempotency_key=key)
        return ok(
            "inspection_ai_suggest",
            task=_task_brief(task),
            pending_confirm=True,
            idempotency_key=key,
        )

    def close_high_risk_event(self, state: GraphState) -> dict[str, Any]:
        """高风险：智能体不得关闭安防事件，只转授权人工（PRD §6.4 / R-04）。"""
        assert_level("close_high_risk_event", OperationLevel.WRITE_HIGH_RISK)
        return handover(
            "close_high_risk_event",
            "高风险安防事件的等级确认与关闭需授权管理人员在业务端完成。",
            event_id=state.slots.get("event_id"),
            risk_level=state.slots.get("risk_level"),
        )


def build_inspection_tools(
    task_service: Any,
    event_service: Any,
    context_provider: ContextProvider,
) -> dict[str, Tool]:
    toolset = InspectionToolSet(task_service, event_service, context_provider)
    return {
        "__prepare_inspection__": toolset.prepare_inspection,
        "inspection_list": toolset.inspection_list,
        "inspection_get_task": toolset.inspection_get_task,
        "inspection_get_event": toolset.inspection_get_event,
        "inspection_create": toolset.inspection_create,
        "inspection_create_task": toolset.inspection_create,
        "inspection_start_task": toolset.inspection_start_task,
        "inspection_add_record": toolset.inspection_add_record,
        "inspection_submit_record": toolset.inspection_submit_record,
        "inspection_submit_records": toolset.inspection_submit_records,
        "inspection_ai_suggest": toolset.inspection_ai_suggest,
        "security_event_create": toolset.security_event_create,
        "security_event_submit_disposal": toolset.security_event_submit_disposal,
        "close_high_risk_event": toolset.close_high_risk_event,
    }
