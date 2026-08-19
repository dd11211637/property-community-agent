"""服务端确认参数构造 —— 把"当前待确认动作"映射为 (action, parameters)。

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
from property_agent.agent.tools.repair import normalize_repair_category
from property_agent.announcement.domain.enums import AnnouncementAction
from property_agent.repair.domain.enums import Urgency


class ParameterDerivationError(RuntimeError):
    """构造确认参数失败（缺少必需槽位 / 公告版本冲突）。"""


def _announcement_params(
    tool: str, state: GraphState, announcement_service: Any
) -> tuple[str, dict[str, Any]]:
    """公告发布 / 定时发布的确认参数；前置校验 ``expected_version``。"""
    context = _resolve_request_context(state)
    announcement = announcement_service.get(
        UUID(str(state.slots["announcement_id"])), context
    )
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
    return (
        "SECURITY_EVENT_CREATE",
        {
            "event_type": str(state.slots.get("event_type") or "OTHER"),
            "risk_level": str(state.slots.get("risk_level") or "MEDIUM"),
            "location": str(state.slots.get("location") or ""),
        },
    )


def _repair_create_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    urgency_value = str(state.slots.get("urgency") or "NORMAL").upper()
    category = normalize_repair_category(state.slots.get("category"))
    try:
        urgency = Urgency(urgency_value)
    except ValueError:
        urgency = Urgency.NORMAL
    return (
        "CREATE_WORK_ORDER",
        {
            "house_id": state.current_house_id,
            "category": category,
            "location": str(state.slots.get("location") or ""),
            "description": str(state.slots.get("description") or ""),
            "urgency": urgency,
            "attachment_ids": (),
        },
    )


def _billing_consult_params(state: GraphState) -> tuple[str, dict[str, Any]]:
    return (
        "CREATE_CONSULTATION",
        {
            "subject": str(state.slots.get("subject") or ""),
            "description": str(state.slots.get("description") or ""),
            "bill_id": str(state.slots.get("bill_id") or ""),
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
    if tool == "inspection_submit_records":
        return _submit_records_params(state)
    if tool == "security_event_create":
        return _security_event_params(state)
    if tool == "billing_consult":
        return _billing_consult_params(state)
    if tool == "repair_create":
        return _repair_create_params(state)
    return _agent_default_params(state)


def _resolve_request_context(state: GraphState) -> Any:
    """注入可信请求上下文（仅供内部使用，避免循环 import container）。"""
    from property_agent.platform.container import resolve_agent_request_context

    return resolve_agent_request_context(state)