"""报修工具 — 只调用 ``WorkOrderService`` 公开方法（PRD §6.1 / §6.5.2）。

- ``repair_list`` / ``repair_get``：只读
- ``repair_create``：写-低风险，必须先确认；高风险报修由 Service 拒绝下单并
  转人工工单，工具把它翻译成接管指令而不是伪装成成功。
"""

from typing import Any
from uuid import UUID

from property_agent.agent.policies import OperationLevel
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import (
    ContextProvider,
    Tool,
    ToolPreconditionError,
    assert_level,
    handover,
    idempotency_key,
    ok,
    require_confirmation,
    require_slot,
)
from property_agent.repair.application.commands import (
    CreateWorkOrderCommand,
    WorkOrderSearch,
)
from property_agent.repair.domain.enums import RepairCategory, Urgency
from property_agent.repair.domain.errors import BusinessError


def _brief(work_order: Any) -> dict[str, Any]:
    """只回传展示所需的事实字段，不外泄整个聚合。"""
    return {
        "id": str(work_order.id),
        "business_no": getattr(work_order, "business_no", None),
        "status": str(getattr(work_order, "status", "")),
        "category": str(getattr(work_order, "category", "")),
        "location": getattr(work_order, "location", None),
        "urgency": str(getattr(work_order, "urgency", "")),
    }


def _as_category(value: Any) -> RepairCategory:
    if isinstance(value, RepairCategory):
        return value
    try:
        return RepairCategory(str(value).upper())
    except ValueError:
        return RepairCategory.OTHER


def _as_urgency(value: Any) -> Urgency:
    if isinstance(value, Urgency):
        return value
    try:
        return Urgency(str(value).upper())
    except ValueError:
        return Urgency.NORMAL


def build_repair_tools(service: Any, context_provider: ContextProvider) -> dict[str, Tool]:
    def repair_list(state: GraphState) -> dict[str, Any]:
        assert_level("repair_list", OperationLevel.READ)
        context = context_provider(state)
        statuses = tuple(state.slots.get("statuses") or ())
        search = WorkOrderSearch(
            house_id=state.current_house_id,
            statuses=statuses,
            limit=int(state.slots.get("limit") or 20),
        )
        items = service.search(search, context)
        return ok("repair_list", count=len(items), items=[_brief(i) for i in items])

    def repair_get(state: GraphState) -> dict[str, Any]:
        assert_level("repair_get", OperationLevel.READ)
        context = context_provider(state)
        raw = require_slot(state, "work_order_id", "repair_get")
        work_order = service.get(UUID(str(raw)), context)
        return ok("repair_get", work_order=_brief(work_order))

    def repair_create(state: GraphState) -> dict[str, Any]:
        assert_level("repair_create", OperationLevel.WRITE_LOW_RISK)
        token = require_confirmation(state, "repair_create")
        context = context_provider(state)
        if state.current_house_id is None:
            raise ToolPreconditionError("repair_create 需要先选择当前房屋")

        command = CreateWorkOrderCommand(
            house_id=state.current_house_id,
            category=_as_category(require_slot(state, "category", "repair_create")),
            location=str(require_slot(state, "location", "repair_create")),
            description=str(require_slot(state, "description", "repair_create")),
            urgency=_as_urgency(state.slots.get("urgency")),
            confirmation_token=token,
        )
        key = idempotency_key(
            state,
            "repair_create",
            {
                "house_id": command.house_id,
                "category": command.category,
                "location": command.location,
                "description": command.description,
                "urgency": command.urgency,
            },
        )
        try:
            work_order = service.create(command, context, idempotency_key=key)
        except BusinessError as exc:
            if exc.code == "HANDOVER_REQUIRED":
                # 高风险报修不会变成普通工单（PRD §6.1），照实回传接管信息。
                return handover(
                    "repair_create",
                    exc.message,
                    **(exc.details or {}),
                )
            raise
        return ok("repair_create", work_order=_brief(work_order), idempotency_key=key)

    return {
        "repair_list": repair_list,
        "repair_get": repair_get,
        "repair_create": repair_create,
    }
