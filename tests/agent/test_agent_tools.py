"""PRD §6.5.2 工具层测试。

验证四件事：
1. 工具只经由公开 Application Service 落库（真实服务 + 真实幂等/权限校验）；
2. 写-低风险工具在没有确认令牌时**拒绝执行**（A-03 确认前不落库）；
3. 同一会话重复执行同一写操作命中幂等重放，不产生第二个业务对象（A-04）；
4. 写-高风险工具（公告发布 / 关闭高风险事件）永不执行，只返回接管指令。
"""

from uuid import UUID, uuid4

import pytest

from property_agent.agent.state import GraphState
from property_agent.agent.tools.announcement import build_announcement_tools
from property_agent.agent.tools.base import ToolPreconditionError
from property_agent.agent.tools.billing import build_billing_tools
from property_agent.agent.tools.inspection import build_inspection_tools
from property_agent.agent.tools.repair import build_repair_tools
from property_agent.billing.errors import BillingError
from property_agent.inspection.application.ports import (
    RequestContext as InspectionContext,
)
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.enums import Role as InspectionRole
from tests.inspection_support import Harness as InspectionHarness


def make_state(ids, **slots) -> GraphState:
    state = GraphState(
        conversation_id="conv-tools-1",
        actor_id=ids.resident,
        community_id=ids.community,
        current_house_id=ids.house,
    )
    state.slots.update(slots)
    return state


# ============================== 报修工具 ==============================


def test_repair_create_requires_confirmation(service, ids, resident_context):
    tools = build_repair_tools(service, lambda _s: resident_context)
    state = make_state(
        ids, category="WATER_PLUMBING", location="厨房", description="水管漏水"
    )
    # 未确认 -> 工具直接拒绝，业务侧不会收到任何调用
    with pytest.raises(ToolPreconditionError):
        tools["repair_create"](state)

    state.confirmation_token = "confirmed"
    result = tools["repair_create"](state)
    assert result["ok"] is True
    assert result["data"]["work_order"]["location"] == "厨房"


def test_repair_create_is_idempotent_within_conversation(
    service, harness, ids, resident_context
):
    tools = build_repair_tools(service, lambda _s: resident_context)
    state = make_state(
        ids, category="ELECTRICAL", location="客厅", description="插座没电"
    )
    state.confirmation_token = "confirmed"

    first = tools["repair_create"](state)
    second = tools["repair_create"](state)

    assert first["data"]["work_order"]["id"] == second["data"]["work_order"]["id"]
    assert len(harness.state.orders) == 1


def test_repair_high_risk_returns_handover_not_work_order(
    service, harness, ids, resident_context
):
    tools = build_repair_tools(service, lambda _s: resident_context)
    state = make_state(
        ids,
        category="ELEVATOR",
        location="1 号楼电梯",
        description="有人被困",
        urgency="HIGH_RISK",
    )
    state.confirmation_token = "confirmed"

    result = tools["repair_create"](state)

    assert result["ok"] is False
    assert result["handover_required"] is True
    assert result["detail"].get("handover_ticket_id")
    assert harness.state.orders == {}


def test_repair_list_is_read_only(service, ids, resident_context):
    tools = build_repair_tools(service, lambda _s: resident_context)
    state = make_state(ids)
    # 只读工具无需确认令牌
    result = tools["repair_list"](state)
    assert result["ok"] is True
    assert result["data"]["count"] == 0


# ============================== 公告工具（高风险） ==============================


class _ExplodingAnnouncementService:
    """任何真实调用都视为越界：高风险工具不允许触达业务服务。"""

    def publish(self, *args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("announce_publish 不得调用业务发布服务")

    def create_draft(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("工具层不应在此路径创建草稿")


def test_announce_publish_never_executes(ids):
    tools = build_announcement_tools(
        _ExplodingAnnouncementService(), lambda _s: object()
    )
    state = make_state(ids, title="停水通知", category="SAFETY")

    result = tools["announce_publish"](state)

    assert result["ok"] is False
    assert result["handover_required"] is True
    assert "授权" in result["reason"]


# ============================== 账单工具 ==============================


class _FakeBillingService:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[str] = []

    def list_bills(self, ctx, db, *, fee_type=None, period=None):
        self.calls.append("list_bills")
        if self.unavailable:
            raise BillingError("BILLING_SOURCE_UNAVAILABLE", "账单服务暂时不可用", 503)
        return [
            type(
                "B",
                (),
                {
                    "bill_id": "B-1",
                    "fee_type": "PROPERTY",
                    "period": "2026-07",
                    "amount": "120.00",
                    "status": "UNPAID",
                    "due_date": "2026-08-10",
                },
            )()
        ]


class _FakeConsultationService:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def create_draft(self, ctx, db, *, subject, description, bill_id, idempotency_key):
        self.keys.append(idempotency_key)
        return type(
            "T",
            (),
            {"id": "C-1", "subject": subject, "status": "DRAFT", "bill_id": bill_id},
        )()


def test_billing_query_reports_real_unavailable_state(ids):
    billing = _FakeBillingService(unavailable=True)
    tools = build_billing_tools(
        billing, _FakeConsultationService(), lambda _s: object(), lambda _s: object()
    )
    state = make_state(ids, query_type="list")

    result = tools["billing_query"](state)

    # 源不可用时不编造账单数字（R-02）
    assert result["ok"] is False
    assert result["error_code"] == "BILLING_SOURCE_UNAVAILABLE"


def test_billing_consult_requires_confirmation_and_stable_key(ids):
    consultation = _FakeConsultationService()
    tools = build_billing_tools(
        _FakeBillingService(), consultation, lambda _s: object(), lambda _s: object()
    )
    state = make_state(ids, subject="物业费有疑问", description="7 月金额比上月高")

    with pytest.raises(ToolPreconditionError):
        tools["billing_consult"](state)
    assert consultation.keys == []

    state.confirmation_token = "confirmed"
    tools["billing_consult"](state)
    tools["billing_consult"](state)

    # 相同会话 + 相同参数 => 相同幂等键，业务侧据此重放
    assert len(consultation.keys) == 2
    assert consultation.keys[0] == consultation.keys[1]
    assert len(consultation.keys[0]) <= 128


# ============================== 巡检与安防工具 ==============================


@pytest.fixture
def inspection_env():
    community = uuid4()
    manager = uuid4()
    security = uuid4()
    harness = InspectionHarness(security_workers={security}, duty_users=[uuid4()])
    task_service = InspectionTaskService(harness.uow)
    event_service = SecurityEventService(harness.uow)
    context = InspectionContext(
        actor_id=manager,
        community_id=community,
        roles=frozenset({InspectionRole.MANAGER}),
        request_id="req_agent_tools",
    )
    tools = build_inspection_tools(task_service, event_service, lambda _s: context)
    return harness, tools, community, manager


def _inspection_state(community: UUID, actor: UUID, **slots) -> GraphState:
    state = GraphState(
        conversation_id="conv-inspection-1", actor_id=actor, community_id=community
    )
    state.slots.update(slots)
    return state


def test_inspection_create_and_ai_suggest_stay_pending(inspection_env):
    harness, tools, community, manager = inspection_env
    state = _inspection_state(
        community,
        manager,
        title="夜间巡检",
        description="东区夜间例行巡检",
        route_points=("A1", "A2"),
    )
    state.confirmation_token = "confirmed"

    created = tools["inspection_create"](state)
    assert created["ok"] is True
    task_id = created["data"]["task"]["id"]

    suggest_state = _inspection_state(
        community, manager, task_id=task_id, point="A1", finding="配电箱门未关"
    )
    suggest_state.confirmation_token = "confirmed"
    suggested = tools["inspection_ai_suggest"](suggest_state)

    # AI 建议只进待人工确认区，不会自动生成安防事件（PRD §6.4）
    assert suggested["data"]["pending_confirm"] is True
    assert suggested["data"]["task"]["ai_pending_confirm"] is True
    assert harness.state.events == {}


def test_close_high_risk_event_never_executes(inspection_env):
    _harness, tools, community, manager = inspection_env
    state = _inspection_state(
        community, manager, event_id=str(uuid4()), risk_level="HIGH_RISK"
    )

    result = tools["close_high_risk_event"](state)

    assert result["ok"] is False
    assert result["handover_required"] is True


def test_inspection_list_requires_no_confirmation(inspection_env):
    _harness, tools, community, manager = inspection_env
    state = _inspection_state(community, manager)

    result = tools["inspection_list"](state)

    assert result["ok"] is True
    assert result["data"]["target"] == "task"
    assert result["data"]["count"] == 0
