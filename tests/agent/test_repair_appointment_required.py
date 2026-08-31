"""报修创建必须收集预约上门时间（修复“未说明时间即建单”缺陷）。

回归测试：
- `RepairCreateInput.appointment_at` 为必填槽位；缺少时输入校验失败，
  编排层据此追问，避免出现“用户未提供预约时间就创建工单”的问题。
- 用户提供具体时间或“稍后协商”时，校验通过并可进入人工确认（延期预约）。
"""

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from property_agent.agent.capabilities.adapters.repair import RepairCreateInput
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import default_capability_policy
from property_agent.agent.runtime import RuntimeContext
from property_agent.platform.adapters.api.dependencies import RequestContext
from property_agent.platform.context import ExecutionSource


def _runtime():
    request = RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=frozenset({"RESIDENT"}),
        bound_house_ids=frozenset(),
        current_house_id=None,
        request_id="request-1",
        execution_source=ExecutionSource.AGENT,
    )
    return RuntimeContext.from_request_context(request, conversation_id="conversation-1")


def _executor(adapters):
    return CapabilityExecutor(default_capability_registry(), default_capability_policy(), adapters)


def test_repair_create_input_requires_appointment_at():
    with pytest.raises(ValidationError):
        RepairCreateInput(description="厨房漏水", location="厨房", urgency="NORMAL")
    # 允许的具体值：具体时间或延期（None / “稍后协商”解析结果）
    assert (
        RepairCreateInput(
            description="x", location="y", urgency="NORMAL", appointment_at=None
        ).appointment_at
        is None
    )
    assert RepairCreateInput(
        description="x", location="y", appointment_at=datetime(2026, 9, 1, 15, 0)
    ).appointment_at == datetime(2026, 9, 1, 15, 0)


def test_missing_appointment_at_blocks_creation_and_triggers_ask():
    adapter = SimpleNamespace(called=False)

    def _adapter(_request, _runtime):
        adapter.called = True

        class AdapterResult:
            def model_dump(self):
                return {"data": {"work_order": {"id": "1"}}}

        return AdapterResult()

    result = _executor({"repair_create": _adapter}).execute(
        "repair_create",
        {"description": "厨房漏水", "location": "厨房"},
        _runtime(),
    )
    assert result.ok is False
    assert result.error.code == "INVALID_CAPABILITY_INPUT"
    assert adapter.called is False


def test_deferred_appointment_at_reaches_confirmation():
    def _adapter(_request, _runtime):
        class AdapterResult:
            def model_dump(self):
                return {"data": {"work_order": {"id": "1"}}}

        return AdapterResult()

    for value in (None, datetime(2026, 9, 1, 15, 0)):
        result = _executor({"repair_create": _adapter}).execute(
            "repair_create",
            {"description": "厨房漏水", "location": "厨房", "appointment_at": value},
            _runtime(),
        )
        assert result.ok is False
        assert result.error.code == "HITL_CONFIRMATION_REQUIRED"
