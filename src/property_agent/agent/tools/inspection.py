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

from property_agent.agent.capabilities.adapters.inspection import InspectionAdapter
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import CapabilityWriteContext
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import default_capability_policy
from property_agent.agent.inspection_compatibility import (
    apply_event_risk_floor,
    inspection_action,
    project_inspection_context,
)
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
from property_agent.agent.tools.capability_bridge import invoke_capability
from property_agent.agent.tools.inspection_presenters import _event_brief, _task_brief
from property_agent.agent.working_state import synchronize_typed_domain
from property_agent.inspection.application.commands import (
    InspectionTaskSearch,
    SecurityEventSearch,
)
from property_agent.inspection.domain.enums import (
    EventRiskLevel,
    EventType,
    TaskRecordType,
)


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class InspectionToolSet:
    """Bound inspection and security-event tools with stable public tool names."""

    def __init__(
        self,
        task_service: Any,
        event_service: Any,
        context_provider: ContextProvider,
        capability_executor: CapabilityExecutor | None = None,
        inspection_context_projector: Any = None,
    ) -> None:
        self._task_service = task_service
        self._event_service = event_service
        self._context_provider = context_provider
        self._inspection_context_projector = inspection_context_projector
        adapters = {
            name: InspectionAdapter(task_service, event_service, name)
            for name in (
                "inspection_list",
                "inspection_get_task",
                "inspection_get_event",
                "inspection_create",
                "inspection_start_task",
                "inspection_add_record",
                "inspection_submit_records",
                "inspection_ai_suggest",
                "security_event_create",
                "security_event_submit_disposal",
            )
        }
        self._executor = capability_executor or CapabilityExecutor(
            default_capability_registry(), default_capability_policy(), adapters
        )

    def _invoke(self, state, name, payload, *, confirmed=False, write=None):
        return invoke_capability(
            self._executor,
            self._context_provider,
            state,
            name,
            payload,
            confirmed=confirmed,
            write=write,
            inspection_context_projector=self._inspection_context_projector,
        )

    def prepare_inspection(self, state: GraphState) -> GraphState:
        action = inspection_action(state)
        context = project_inspection_context(
            self._context_provider, self._inspection_context_projector, state
        )
        if action in {"report_event", "create_event", "event_create"}:
            apply_event_risk_floor(state.slots)
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
                synchronize_typed_domain(state)
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
                synchronize_typed_domain(state)
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
        synchronize_typed_domain(state)
        return state

    def inspection_list(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_list", OperationLevel.READ)
        target = str(state.slots.get("target") or "task").lower()
        limit = int(state.slots.get("limit") or 20)
        data = self._invoke(
            state,
            "inspection_list",
            {
                "target": "event" if target == "event" else "task",
                "statuses": tuple(state.slots.get("statuses") or ()),
                "risk_levels": tuple(state.slots.get("risk_levels") or ()),
                "assigned_to_me": bool(state.slots.get("assigned_to_me")),
                "limit": limit,
            },
        )
        return ok("inspection_list", **data)

    def inspection_get_task(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_list", OperationLevel.READ)
        task_id = UUID(str(require_slot(state, "task_id", "inspection_get_task")))
        return ok(
            "inspection_get_task",
            **self._invoke(state, "inspection_get_task", {"task_id": task_id}),
        )

    def inspection_get_event(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_list", OperationLevel.READ)
        event_id = UUID(str(require_slot(state, "event_id", "inspection_get_event")))
        return ok(
            "inspection_get_event",
            **self._invoke(state, "inspection_get_event", {"event_id": event_id}),
        )

    def inspection_create(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_create", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "inspection_create")
        route_points = tuple(state.slots.get("route_points") or ())
        if not route_points:
            route_points = (str(require_slot(state, "point", "inspection_create")),)
        payload = {
            "title": str(require_slot(state, "title", "inspection_create")),
            "description": str(require_slot(state, "description", "inspection_create")),
            "route_points": route_points,
            "point": route_points[0],
            "planned_at": _as_datetime(state.slots.get("planned_at")),
            "due_at": _as_datetime(state.slots.get("due_at")),
        }
        key = idempotency_key(
            state,
            "inspection_create",
            {
                **payload,
            },
        )
        data = self._invoke(
            state,
            "inspection_create",
            payload,
            confirmed=True,
            write=CapabilityWriteContext(token, key, state.approval_ref),
        )
        return ok("inspection_create", **data)

    def inspection_start_task(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_start_task", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "inspection_start_task")
        task_id = UUID(str(require_slot(state, "task_id", "inspection_start_task")))
        expected_version = int(require_slot(state, "expected_version", "inspection_start_task"))
        key = idempotency_key(
            state,
            "inspection_start_task",
            {"task_id": task_id, "expected_version": expected_version},
        )
        data = self._invoke(
            state,
            "inspection_start_task",
            {"task_id": task_id, "expected_version": expected_version},
            confirmed=True,
            write=CapabilityWriteContext(token, key, state.approval_ref),
        )
        return ok("inspection_start_task", **data)

    def _record(self, state: GraphState, *, final: bool) -> dict[str, Any]:
        tool = "inspection_submit_records" if final else "inspection_add_record"
        assert_level(tool, OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, tool)
        task_id = UUID(str(require_slot(state, "task_id", tool)))
        expected_version = int(require_slot(state, "expected_version", tool))
        record_type = TaskRecordType(
            str(state.slots.get("record_type") or ("COMPLETION" if final else "POINT_RECORD"))
        )
        note = str(require_slot(state, "note", tool))
        point = str(require_slot(state, "point", tool))
        key = idempotency_key(
            state,
            tool,
            {
                "task_id": task_id,
                "expected_version": expected_version,
                "point": point,
                "note": note,
                "record_type": record_type.value,
            },
        )
        data = self._invoke(
            state,
            tool,
            {
                "task_id": task_id,
                "expected_version": expected_version,
                "point": point,
                "note": note,
                "record_type": record_type.value,
            },
            confirmed=True,
            write=CapabilityWriteContext(token, key, state.approval_ref),
        )
        return ok(tool, **data)

    def inspection_add_record(self, state: GraphState) -> dict[str, Any]:
        return self._record(state, final=False)

    def inspection_submit_records(self, state: GraphState) -> dict[str, Any]:
        return self._record(state, final=True)

    def security_event_create(self, state: GraphState) -> dict[str, Any]:
        assert_level("security_event_create", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "security_event_create")
        apply_event_risk_floor(state.slots)
        event_type = EventType(str(state.slots["event_type"]))
        risk_level = EventRiskLevel(str(state.slots["risk_level"]))
        payload = {
            "source_task_id": UUID(str(state.slots["task_id"]))
            if state.slots.get("task_id")
            else None,
            "event_type": event_type.value,
            "risk_level": risk_level.value,
            "location": str(require_slot(state, "location", "security_event_create")),
            "description": str(require_slot(state, "description", "security_event_create")),
        }
        key = idempotency_key(
            state,
            "security_event_create",
            {
                "event_type": event_type.value,
                "risk_level": risk_level.value,
                "location": payload["location"],
                "description": payload["description"],
            },
        )
        data = self._invoke(
            state,
            "security_event_create",
            payload,
            confirmed=True,
            write=CapabilityWriteContext(token, key, state.approval_ref),
        )
        if data["handover_required"]:
            state.handover_required = True
        data["handover_required"] = state.handover_required
        return ok("security_event_create", **data)

    def security_event_submit_disposal(self, state: GraphState) -> dict[str, Any]:
        assert_level("security_event_submit_disposal", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "security_event_submit_disposal")
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
        data = self._invoke(
            state,
            "security_event_submit_disposal",
            {"event_id": event_id, "expected_version": expected_version, "note": note},
            confirmed=True,
            write=CapabilityWriteContext(token, key, state.approval_ref),
        )
        return ok("security_event_submit_disposal", **data)

    def inspection_submit_record(self, state: GraphState) -> dict[str, Any]:
        tool = "inspection_add_record"
        assert_level(tool, OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, tool)
        task_id = UUID(str(require_slot(state, "task_id", tool)))
        is_supplement = bool(state.slots.get("is_supplement"))
        expected_version = int(require_slot(state, "expected_version", tool))
        point = str(require_slot(state, "point", tool))
        note = str(state.slots.get("note") or "")
        key = idempotency_key(
            state,
            tool,
            {
                "task_id": task_id,
                "point": point,
                "note": note,
                "is_supplement": is_supplement,
                "expected_version": expected_version,
            },
        )
        data = self._invoke(
            state,
            tool,
            {
                "task_id": task_id,
                "expected_version": expected_version,
                "point": point,
                "note": note,
                "record_type": (
                    TaskRecordType.SUPPLEMENT.value
                    if is_supplement
                    else TaskRecordType.POINT_RECORD.value
                ),
                "is_supplement": is_supplement,
                "actual_time": _as_datetime(state.slots.get("actual_time")),
                "supplement_reason": state.slots.get("supplement_reason"),
            },
            confirmed=True,
            write=CapabilityWriteContext(token, key, state.approval_ref),
        )
        return ok(tool, **data)

    def inspection_ai_suggest(self, state: GraphState) -> dict[str, Any]:
        assert_level("inspection_ai_suggest", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "inspection_ai_suggest")
        task_id = UUID(str(require_slot(state, "task_id", "inspection_ai_suggest")))
        point = str(require_slot(state, "point", "inspection_ai_suggest"))
        finding = str(require_slot(state, "finding", "inspection_ai_suggest"))
        severity = str(state.slots.get("severity") or "MEDIUM")
        model = str(state.slots.get("model") or "inspection-ai")
        key = idempotency_key(
            state,
            "inspection_ai_suggest",
            {
                "task_id": task_id,
                "point": point,
                "finding": finding,
                "severity": severity,
            },
        )
        data = self._invoke(
            state,
            "inspection_ai_suggest",
            {
                "task_id": task_id,
                "point": point,
                "finding": finding,
                "severity": severity,
                "model": model,
            },
            confirmed=True,
            write=CapabilityWriteContext(token, key, state.approval_ref),
        )
        return ok("inspection_ai_suggest", **data)

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
    capability_executor: CapabilityExecutor | None = None,
    inspection_context_projector: Any = None,
) -> dict[str, Tool]:
    toolset = InspectionToolSet(
        task_service,
        event_service,
        context_provider,
        capability_executor,
        inspection_context_projector,
    )
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
