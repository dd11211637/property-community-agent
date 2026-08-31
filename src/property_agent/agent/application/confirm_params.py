"""Agent 服务端确认参数构造 —— 把"当前待确认动作"映射为 (action, parameters)。

``confirmation_token_provider`` 用本模块按 ``pending_action['tool']`` 决定
``(action, parameters)``，保持与业务 Service 的 ``canonical_hash(asdict(command))``
完全一致：同一个 ``(action, parameters)`` 集合对 token 与审批都生成同一
``params_hash``，杜绝"凭据通过但审批失败"或反之。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from property_agent.agent.state import GraphState
from property_agent.announcement.domain.classification import classify_announcement_category
from property_agent.announcement.domain.enums import AnnouncementAction
from property_agent.announcement.domain.policies import normalize_audience_condition
from property_agent.inspection.application.commands import (
    AddAiSuggestionCommand,
    CreateInspectionTaskCommand,
    ExecuteEventActionCommand,
    ExecuteTaskActionCommand,
)
from property_agent.inspection.domain.classification import normalize_security_event
from property_agent.inspection.domain.enums import EventAction, TaskAction, TaskRecordType
from property_agent.repair.domain.classification import classify_repair_category
from property_agent.repair.domain.enums import Urgency


class ParameterDerivationError(RuntimeError):
    """构造确认参数失败（缺少必需槽位 / 公告版本冲突）。"""


def _announcement_params(
    tool: str, state: GraphState, announcement_service: Any
) -> tuple[str, dict[str, Any]]:
    """公告发布 / 定时发布的确认参数；前置校验 ``expected_version``。"""
    context = _resolve_request_context(state)
    announcement = announcement_service.get(UUID(str(state.slots["announcement_id"])), context)
    reviewed_version = int(state.slots["expected_version"])
    if reviewed_version != announcement.version:
        raise ParameterDerivationError("公告内容已发生变化，请重新查看后再确认发布。")
    if tool == "announce_publish":
        return (
            "ANNOUNCEMENT_PUBLISH",
            {
                "announcement_id": announcement.id,
                "expected_version": announcement.version,
                "action": AnnouncementAction.PUBLISH,
            },
        )
    scheduled_at = datetime.fromisoformat(str(state.slots["scheduled_at"]))
    return (
        "ANNOUNCEMENT_SCHEDULE",
        {
            "announcement_id": announcement.id,
            "expected_version": announcement.version,
            "scheduled_at": scheduled_at,
        },
    )


def _submit_records_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    return (
        "INSPECTION_TASK_SUBMIT_RECORDS",
        {
            "note": str(state.slots.get("note") or ""),
            "record_type": str(state.slots.get("record_type") or "COMPLETION"),
            "point": str(state.slots.get("point") or ""),
        },
    )


def _security_event_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    pending = dict((state.pending_action or {}).get("params") or {})
    normalized = normalize_security_event(
        str(pending.get("description") or state.slots.get("description") or ""),
        pending.get("risk_level", state.slots.get("risk_level")),
    )
    return (
        "SECURITY_EVENT_CREATE",
        {
            "event_type": normalized.event_type.value,
            "risk_level": normalized.risk_level.value,
            "location": str(pending.get("location") or state.slots.get("location") or ""),
        },
    )


def _announcement_create_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    title = str(state.slots.get("title") or "")
    body = str(state.slots.get("body") or "")
    return (
        "ANNOUNCEMENT_CREATE",
        {
            "title": title.strip(),
            "body": body.strip(),
            "category": classify_announcement_category(title, body),
            "audience": normalize_audience_condition(state.slots.get("audience") or {}),
        },
    )


def _inspection_create_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    route_points = tuple(state.slots.get("route_points") or ())
    if not route_points:
        route_points = (str(state.slots.get("point") or ""),)
    command = CreateInspectionTaskCommand(
        title=str(state.slots.get("title") or ""),
        description=str(state.slots.get("description") or ""),
        route_points=route_points,
        planned_at=_optional_datetime(state.slots.get("planned_at")),
        due_at=_optional_datetime(state.slots.get("due_at")),
    )
    return "INSPECTION_TASK_CREATE", _without_approval(command)


def _inspection_task_action_params(state: GraphState, tool: str) -> tuple[str, dict[str, Any]]:
    action = TaskAction.START if tool == "inspection_start_task" else TaskAction.ADD_RECORD
    is_supplement = bool(state.slots.get("is_supplement"))
    record_type = None
    if action == TaskAction.ADD_RECORD:
        default = "SUPPLEMENT" if is_supplement else "POINT_RECORD"
        record_type = TaskRecordType(str(state.slots.get("record_type") or default))
    command = ExecuteTaskActionCommand(
        action=action,
        expected_version=int(state.slots.get("expected_version") or 0),
        note=state.slots.get("note"),
        record_type=record_type,
        point=state.slots.get("point"),
        is_supplement=is_supplement,
        actual_time=_optional_datetime(state.slots.get("actual_time")),
        supplement_reason=state.slots.get("supplement_reason"),
    )
    params = {"task_id": UUID(str(state.slots["task_id"])), **_without_approval(command)}
    return f"INSPECTION_TASK_{action.value}", params


def _inspection_ai_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    command = AddAiSuggestionCommand(
        point=str(state.slots.get("point") or ""),
        finding=str(state.slots.get("finding") or ""),
        severity=str(state.slots.get("severity") or "MEDIUM"),
        model=str(state.slots.get("model") or "inspection-ai"),
    )
    return (
        "INSPECTION_TASK_ADD_AI_SUGGESTION",
        {"task_id": UUID(str(state.slots["task_id"])), **_without_approval(command)},
    )


def _security_disposal_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    command = ExecuteEventActionCommand(
        action=EventAction.SUBMIT_DISPOSAL,
        expected_version=int(state.slots.get("expected_version") or 0),
        note=str(state.slots.get("note") or ""),
    )
    return (
        "SECURITY_EVENT_SUBMIT_DISPOSAL",
        {"event_id": UUID(str(state.slots["event_id"])), **_without_approval(command)},
    )


def _without_approval(command: Any) -> dict[str, Any]:
    from dataclasses import asdict

    values = asdict(command)
    values.pop("confirmation_token", None)
    values.pop("approval_ref", None)
    return values


def _optional_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _repair_create_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    pending = dict((state.pending_action or {}).get("params") or {})
    description = str(pending.get("description") or state.slots.get("description") or "")
    location = str(pending.get("location") or state.slots.get("location") or "")
    urgency_value = str(pending.get("urgency") or state.slots.get("urgency") or "NORMAL").upper()
    category = classify_repair_category(description)
    try:
        urgency = Urgency(urgency_value)
    except ValueError:
        urgency = Urgency.NORMAL
    return (
        "CREATE_WORK_ORDER",
        {
            "house_id": state.current_house_id,
            "category": category,
            "location": location,
            "description": description,
            "urgency": urgency,
            "attachment_ids": (),
        },
    )


def _billing_consult_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    pending = dict((state.pending_action or {}).get("params") or {})
    raw_bill_id = pending.get("bill_id", state.slots.get("bill_id"))
    return (
        "CREATE_CONSULTATION",
        {
            "subject": str(pending.get("subject") or state.slots.get("subject") or ""),
            "description": str(pending.get("description") or state.slots.get("description") or ""),
            # Match ConsultationService's authoritative optional-field contract:
            # an absent bill association is None, not an empty string.
            "bill_id": str(raw_bill_id) if raw_bill_id else None,
        },
    )


def _agent_default_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    """AGENT_* 默认分支：参数指纹只覆盖 ``pending_action.params``。"""
    pending = state.pending_action or {}
    params = dict(pending.get("params") or {})
    tool = str(pending.get("tool") or "")
    return f"AGENT_{tool.upper()}", params


def derive_confirmation_params(
    state: GraphState, *, announcement_service: Any
) -> tuple[str, dict[str, Any]]:
    """根据 ``state.pending_action['tool']`` 派生 ``(action, parameters)``。

    返回的 ``parameters`` 是后续同时喂给 ``ConfirmationService.generate_token``
    与 ``ApprovalService.create_pending`` 的同一份字典——保证 token 与审批的
    ``params_hash`` 完全对齐，业务 UoW 内消费时不会因微小漂移被拒。
    """
    pending = state.pending_action or {}
    tool = str(pending.get("tool") or "")
    if tool in {"announce_publish", "announcement_schedule_publish"}:
        return _announcement_params(tool, state, announcement_service)
    if tool == "announcement_create_draft":
        return _announcement_create_params(state)
    if tool == "inspection_submit_records":
        return _submit_records_params(state)
    if tool == "security_event_create":
        return _security_event_params(state)
    if tool in {
        "inspection_create",
        "inspection_create_task",
    }:
        return _inspection_create_params(state)
    if tool in {"inspection_start_task", "inspection_add_record", "inspection_submit_record"}:
        return _inspection_task_action_params(state, tool)
    if tool == "inspection_ai_suggest":
        return _inspection_ai_params(state)
    if tool == "security_event_submit_disposal":
        return _security_disposal_params(state)
    if tool == "billing_consult":
        return _billing_consult_params(state)
    if tool == "repair_create":
        return _repair_create_params(state)
    return _agent_default_params(state)


def _resolve_request_context(state: GraphState) -> Any:
    """注入可信请求上下文（仅供内部使用，避免循环 import container）。"""
    from property_agent.platform.container import resolve_agent_request_context

    return resolve_agent_request_context(state)
