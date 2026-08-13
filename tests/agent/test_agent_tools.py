"""PRD §6.5.2 工具层测试。

验证四件事：
1. 工具只经由公开 Application Service 落库（真实服务 + 真实幂等/权限校验）；
2. 写-低风险工具在没有确认令牌时**拒绝执行**（A-03 确认前不落库）；
3. 同一会话重复执行同一写操作命中幂等重放，不产生第二个业务对象（A-04）；
4. 公告发布必须经过管理者审稿版本绑定与确认；关闭高风险事件仍只转人工。
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
from property_agent.inspection.domain.enums import TaskStatus
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
    state = make_state(ids, category="漏电", location="厨房", description="线路漏电")
    # 未确认 -> 工具直接拒绝，业务侧不会收到任何调用
    with pytest.raises(ToolPreconditionError):
        tools["repair_create"](state)

    state.confirmation_token = "confirmed"
    result = tools["repair_create"](state)
    assert result["ok"] is True
    assert result["data"]["work_order"]["location"] == "厨房"
    assert result["data"]["work_order"]["category"] == "ELECTRICAL"


def test_repair_create_ignores_non_authoritative_category(service, ids, resident_context):
    tools = build_repair_tools(service, lambda _s: resident_context)
    state = make_state(
        ids,
        category="WATER_PLUMBING",
        location="客厅",
        description="客厅插座频繁跳闸",
    )
    state.confirmation_token = "confirmed"

    result = tools["repair_create"](state)

    assert result["data"]["work_order"]["category"] == "ELECTRICAL"


def test_repair_create_is_idempotent_within_conversation(service, harness, ids, resident_context):
    tools = build_repair_tools(service, lambda _s: resident_context)
    state = make_state(ids, category="ELECTRICAL", location="客厅", description="插座没电")
    state.confirmation_token = "confirmed"

    first = tools["repair_create"](state)
    second = tools["repair_create"](state)

    assert first["data"]["work_order"]["id"] == second["data"]["work_order"]["id"]
    assert len(harness.state.orders) == 1


def test_repair_get_accepts_business_number_and_returns_timeline(service, ids, resident_context):
    tools = build_repair_tools(service, lambda _s: resident_context)
    create_state = make_state(
        ids,
        category="ELECTRICAL",
        location="客厅",
        description="灯具损坏",
    )
    create_state.confirmation_token = "confirmed"
    created = tools["repair_create"](create_state)["data"]["work_order"]

    query_state = make_state(ids, work_order_id=created["business_no"])
    result = tools["repair_get"](query_state)

    assert result["ok"] is True
    assert result["data"]["work_order"]["business_no"] == created["business_no"]
    assert result["data"]["timeline"]


def test_repair_high_risk_returns_handover_not_work_order(service, harness, ids, resident_context):
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


# ============================== 公告工具（受控写入） ==============================


class _AnnouncementSearchService:
    def __init__(self, items):
        self.items = items

    def search(self, search, context):
        return self.items


class _AnnouncementRevisionGateway:
    def revise_announcement(self, *, draft, audience, instruction):
        assert draft["body"] == "明天暂停供水。"
        assert audience == {}
        assert instruction.startswith("改成明天上午9点至下午4点停水")
        return {
            "title": draft["title"],
            "body": "明天上午9点至下午4点暂停供水。",
            "category": draft["category"],
        }


class _AnnouncementDraftGateway:
    def draft_announcement(self, *, topic, audience, requirements):
        assert topic == "WATER_OUTAGE"
        assert audience == {}
        assert "明天" in requirements
        assert "事项日期为2026年8月14日" in requirements
        return {
            "title": "关于明日停水的通知",
            "body": "明天暂停供水。",
            "category": "MAINTENANCE",
        }


def test_announcement_draft_materializes_trusted_target_date(ids):
    tools = build_announcement_tools(
        _AnnouncementSearchService([]),
        lambda _s: object(),
        model_gateway=_AnnouncementDraftGateway(),
    )
    state = make_state(
        ids,
        topic="WATER_OUTAGE",
        audience={},
        target_date="2026-08-14",
        user_text="明天停水",
    )

    result = tools["announcement_draft"](state)

    assert result["data"]["draft"]["title"] == "关于2026年8月14日停水的通知"
    assert result["data"]["draft"]["body"] == "2026年8月14日暂停供水。"


def test_announcement_draft_recovers_legacy_json_audience_value(ids):
    tools = build_announcement_tools(
        _AnnouncementSearchService([]),
        lambda _s: object(),
        model_gateway=_AnnouncementDraftGateway(),
    )
    state = make_state(
        ids,
        topic="WATER_OUTAGE",
        audience="{}",
        target_date="2026-08-14",
        user_text="明天停水",
    )

    result = tools["announcement_draft"](state)

    assert result["data"]["draft"]["audience"] == {}
    assert state.slots["audience"] == {}


def test_announcement_revision_updates_only_conversation_draft(ids):
    tools = build_announcement_tools(
        _AnnouncementSearchService([]),
        lambda _s: object(),
        model_gateway=_AnnouncementRevisionGateway(),
    )
    state = make_state(
        ids,
        title="停水通知",
        body="明天暂停供水。",
        category="MAINTENANCE",
        audience={},
        revision_instruction="改成明天上午9点至下午4点停水",
    )

    result = tools["announcement_revise"](state)

    assert result["ok"] is True
    assert result["data"]["draft"]["body"] == "明天上午9点至下午4点暂停供水。"
    assert state.slots["action"] == "create"
    assert "revision_instruction" not in state.slots


def test_announcement_list_filters_by_topic_date_and_returns_scope(ids):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    items = [
        SimpleNamespace(
            id=uuid4(),
            business_no="GG-1",
            title="供水维护通知",
            body="1 栋计划停水维护",
            category="MAINTENANCE",
            status="PUBLISHED",
            audience_condition={"building_ids": ["1"]},
            scheduled_at=datetime(2026, 8, 12, 1, tzinfo=UTC),
            published_at=datetime(2026, 8, 11, 1, tzinfo=UTC),
            version=1,
        ),
        SimpleNamespace(
            id=uuid4(),
            business_no="GG-2",
            title="停车通知",
            body="车库维护",
            category="GENERAL",
            status="PUBLISHED",
            audience_condition={},
            scheduled_at=datetime(2026, 8, 12, 1, tzinfo=UTC),
            published_at=datetime(2026, 8, 11, 1, tzinfo=UTC),
            version=1,
        ),
    ]
    tools = build_announcement_tools(_AnnouncementSearchService(items), lambda _s: object())
    state = make_state(ids, topic="WATER_OUTAGE", target_date="2026-08-12")
    state.trusted_context = {"community_name": "幸福小区", "building": "1"}

    result = tools["announcement_list"](state)

    assert result["data"]["count"] == 1
    assert result["data"]["items"][0]["body"] == "1 栋计划停水维护"
    assert result["data"]["query_scope"] == {
        "community_name": "幸福小区",
        "building": "1",
    }


def test_community_knowledge_search_returns_only_matching_published_material(ids):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    items = [
        SimpleNamespace(
            id=uuid4(),
            business_no="GG-K1",
            title="物业联系方式",
            body="服务时间以公告为准。",
            category="GENERAL",
            status="PUBLISHED",
            audience_condition={"building_ids": ["1"]},
            scheduled_at=None,
            published_at=datetime(2026, 8, 10, tzinfo=UTC),
            version=1,
        ),
        SimpleNamespace(
            id=uuid4(),
            business_no="GG-K2",
            title="暑期活动",
            body="社区活动安排。",
            category="GENERAL",
            status="PUBLISHED",
            audience_condition={},
            scheduled_at=None,
            published_at=datetime(2026, 8, 9, tzinfo=UTC),
            version=1,
        ),
    ]
    tools = build_announcement_tools(_AnnouncementSearchService(items), lambda _s: object())
    state = make_state(ids, query="物业电话是多少")
    state.trusted_context = {"community_name": "幸福小区", "building": "1"}

    result = tools["community_knowledge_search"](state)

    assert result["data"]["count"] == 1
    item = result["data"]["items"][0]
    assert item["source_name"] == "物业联系方式"
    assert item["published_at"].startswith("2026-08-10")
    assert item["applicability"] == {"building_ids": ["1"]}
    assert result["data"]["source_scope"] == "PUBLISHED_ANNOUNCEMENTS"


def test_announce_publish_requires_confirmation(ids):
    tools = build_announcement_tools(_AnnouncementSearchService([]), lambda _s: object())
    state = make_state(ids, announcement_id=str(uuid4()), expected_version=3)

    with pytest.raises(ToolPreconditionError):
        tools["announce_publish"](state)


# ============================== 账单工具 ==============================


class _FakeBillingService:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[tuple[str, str | None, str | None]] = []

    def list_bills(self, ctx, db, *, fee_type=None, period=None):
        self.calls.append(("list_bills", fee_type, period))
        if self.unavailable:
            raise BillingError("BILLING_SOURCE_UNAVAILABLE", "账单服务暂时不可用", 503)
        return [
            type(
                "B",
                (),
                {
                    "bill_id": "B-1",
                    "fee_type": "PROPERTY",
                    "bill_period": "2026-07",
                    "total_amount": "120.00",
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


def test_billing_query_maps_real_bill_period_and_total_amount(ids):
    tools = build_billing_tools(
        _FakeBillingService(),
        _FakeConsultationService(),
        lambda _s: object(),
        lambda _s: object(),
    )

    result = tools["billing_query"](make_state(ids, query_type="list"))

    item = result["data"]["items"][0]
    assert item["period"] == "2026-07"
    assert item["total_amount"] == "120.00"
    assert item["amount"] == "120.00"


def test_billing_query_forwards_period_and_returns_filter_fact(ids):
    billing = _FakeBillingService()
    tools = build_billing_tools(
        billing,
        _FakeConsultationService(),
        lambda _s: object(),
        lambda _s: object(),
    )

    result = tools["billing_query"](make_state(ids, query_type="list", period="2026-08"))

    assert billing.calls == [("list_bills", None, "2026-08")]
    assert result["data"]["period"] == "2026-08"


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
    state = GraphState(conversation_id="conv-inspection-1", actor_id=actor, community_id=community)
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
    state = _inspection_state(community, manager, event_id=str(uuid4()), risk_level="HIGH_RISK")

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


def test_prepare_inspection_selects_only_executable_task(inspection_env):
    harness, tools, community, manager = inspection_env
    create = _inspection_state(
        community,
        manager,
        title="唯一任务",
        description="检查消防设备",
        route_points=("1栋大厅",),
    )
    create.confirmation_token = "confirmed"
    created = tools["inspection_create"](create)["data"]["task"]
    task = harness.state.tasks[UUID(created["id"])]
    task.assignee_id = manager
    task.status = TaskStatus.ASSIGNED

    state = _inspection_state(community, manager, action="start_task")
    tools["__prepare_inspection__"](state)

    assert state.slots["task_id"] == created["id"]
    assert state.slots["expected_version"] == task.version


def test_prepare_inspection_returns_task_selection_for_multiple_candidates(inspection_env):
    harness, tools, community, manager = inspection_env
    for index in range(2):
        create = _inspection_state(
            community,
            manager,
            title=f"候选任务{index + 1}",
            description="检查消防设备",
            route_points=(f"{index + 1}栋大厅",),
        )
        create.confirmation_token = "confirmed"
        created = tools["inspection_create"](create)["data"]["task"]
        task = harness.state.tasks[UUID(created["id"])]
        task.assignee_id = manager
        task.status = TaskStatus.ASSIGNED

    state = _inspection_state(community, manager, action="start_task")
    tools["__prepare_inspection__"](state)

    assert "task_id" not in state.slots
    selection = state.slots["_selection_options"]
    assert selection["field"] == "task_id"
    assert len(selection["options"]) == 2


def test_high_risk_floor_cannot_be_downgraded(inspection_env):
    _harness, tools, community, manager = inspection_env
    state = _inspection_state(
        community,
        manager,
        action="report_event",
        event_type="GAS_LEAK",
        risk_level="LOW",
        description="闻到明显燃气泄漏气味",
    )

    tools["__prepare_inspection__"](state)

    assert state.slots["risk_level"] == "HIGH_RISK"
    assert "远离危险区域" in state.slots["safety_notice"]
