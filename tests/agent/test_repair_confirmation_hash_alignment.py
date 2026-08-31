"""报修确认参数指纹对齐 —— PENDING 审批与业务 CONSUME 必须使用同一份哈希。

回归背景：appointment_at 被加入 ``CreateWorkOrderCommand`` 后，
``_repair_create_params`` 仍漏掉该字段，导致 PENDING 审批的 ``params_hash``
（不含 appointment_at）与业务 ``WorkOrderService.create`` 内
``canonical_hash(asdict(command))``（含 appointment_at）永远不一致，
每次确认都被 ``APPROVAL_PARAMS_CHANGED`` (409) 拒绝。

本测试直接断言：对同一组报修参数，``derive_confirmation_params`` 产出的
参数指纹 == ``WorkOrderService.create`` 消费审批时计算的参数指纹。
"""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid4

from property_agent.agent.application.confirm_params import derive_confirmation_params
from property_agent.agent.state import GraphState
from property_agent.platform.application.hashing import canonical_hash
from property_agent.repair.application.commands import CreateWorkOrderCommand
from property_agent.repair.domain.classification import classify_repair_category
from property_agent.repair.domain.enums import Urgency

# derive_confirmation_params 仅在公告分支用到 announcement_service；
# repair_create 分支不需要，传 None 即可。
_NO_ANNOUNCEMENT = None


def _repair_state(*, appointment_at, description="厨房水管漏水", location="厨房") -> GraphState:
    """构造一个停在 repair_create 待确认状态的 GraphState。"""
    house_id = uuid4()
    params = {
        "description": description,
        "location": location,
        "urgency": "NORMAL",
    }
    if appointment_at is not None:
        params["appointment_at"] = appointment_at
    return GraphState(
        conversation_id="conv-hash-align",
        actor_id=uuid4(),
        community_id=uuid4(),
        current_house_id=house_id,
        intent="REPAIR",
        slots={"house_id": str(house_id), **params},
        pending_action={
            "tool": "repair_create",
            "params": params,
            "params_hash": "unused-by-derive",
        },
    )


def _consume_side_hash(*, house_id, description, location, urgency, appointment_at) -> str:
    """复刻 WorkOrderService.create 内的消费侧哈希计算。"""
    command = CreateWorkOrderCommand(
        house_id=house_id,
        category=classify_repair_category(description),
        location=location,
        description=description,
        urgency=Urgency(str(urgency).upper()),
        confirmation_token="token-placeholder",
        approval_ref=None,
        appointment_at=appointment_at,
        attachment_ids=(),
    )
    confirmed_parameters = asdict(command)
    confirmed_parameters.pop("confirmation_token", None)
    confirmed_parameters.pop("approval_ref", None)
    return canonical_hash(confirmed_parameters)


def test_repair_confirmation_hash_matches_consume_side_with_concrete_time():
    """用户提供具体预约时间时，PENDING 与 CONSUME 指纹必须一致。"""
    appointment = datetime(2026, 8, 31, 17, 0)
    state = _repair_state(appointment_at=appointment)
    action, params = derive_confirmation_params(state, announcement_service=_NO_ANNOUNCEMENT)
    assert action == "CREATE_WORK_ORDER"
    pending_hash = canonical_hash(params)
    consume_hash = _consume_side_hash(
        house_id=state.current_house_id,
        description="厨房水管漏水",
        location="厨房",
        urgency="NORMAL",
        appointment_at=appointment,
    )
    assert pending_hash == consume_hash, (
        "PENDING 侧参数指纹与 CONSUME 侧不一致，确认会被 APPROVAL_PARAMS_CHANGED 拒绝"
    )


def test_repair_confirmation_hash_matches_when_appointment_is_iso_string():
    """检查点往返后 appointment_at 可能变成 ISO 字符串；指纹仍须对齐。

    canonical_hash 把 datetime 归一为 .isoformat()（含秒），把字符串原样保留。
    _repair_create_params 用 _optional_datetime 把字符串归一回 datetime，确保
    两侧都走 datetime.isoformat() 路径，避免 "T17:00" 与 "T17:00:00" 不一致。
    """
    state = _repair_state(appointment_at="2026-08-31T17:00")
    action, params = derive_confirmation_params(state, announcement_service=_NO_ANNOUNCEMENT)
    assert action == "CREATE_WORK_ORDER"
    pending_hash = canonical_hash(params)
    # 消费侧 command.appointment_at 是 datetime（pydantic/adapter 解析后），
    # 其 isoformat 为 "2026-08-31T17:00:00"。
    consume_hash = _consume_side_hash(
        house_id=state.current_house_id,
        description="厨房水管漏水",
        location="厨房",
        urgency="NORMAL",
        appointment_at=datetime(2026, 8, 31, 17, 0),
    )
    assert pending_hash == consume_hash


def test_repair_confirmation_hash_matches_when_appointment_deferred():
    """用户选择“稍后协商”时 appointment_at 为 None；指纹仍须对齐。"""
    # None 不进入 pending params（与 RepairSpecialist.project_parameters 行为一致：
    # values.get("appointment_at") 为假值时不加入 params）。
    state = _repair_state(appointment_at=None)
    action, params = derive_confirmation_params(state, announcement_service=_NO_ANNOUNCEMENT)
    assert action == "CREATE_WORK_ORDER"
    pending_hash = canonical_hash(params)
    consume_hash = _consume_side_hash(
        house_id=state.current_house_id,
        description="厨房水管漏水",
        location="厨房",
        urgency="NORMAL",
        appointment_at=None,
    )
    assert pending_hash == consume_hash


def test_repair_confirmation_params_include_appointment_at_key():
    """直接断言 derive 出的参数含 appointment_at 键，防止再次被漏掉。"""
    state = _repair_state(appointment_at=datetime(2026, 8, 31, 17, 0))
    _action, params = derive_confirmation_params(state, announcement_service=_NO_ANNOUNCEMENT)
    assert "appointment_at" in params
    assert isinstance(params["house_id"], UUID)
