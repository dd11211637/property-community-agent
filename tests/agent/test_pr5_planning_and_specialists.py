import json
from types import SimpleNamespace
from uuid import uuid4

import httpx

from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import (
    CapabilityDomainError,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import default_capability_policy
from property_agent.agent.deepseek_gateway import DeepSeekModelGateway
from property_agent.agent.orchestration import (
    ObjectiveClassification,
    PlanStep,
    SpecialistName,
    SpecialistOutcome,
)
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.runtime import PreparedWrite, RuntimeContext
from property_agent.agent.specialists.announcement import AnnouncementSpecialist
from property_agent.agent.specialists.billing import BillingSpecialist
from property_agent.agent.specialists.inspection import InspectionSpecialist
from property_agent.agent.specialists.repair import RepairSpecialist
from property_agent.agent.state import AgentState
from property_agent.platform.adapters.api.dependencies import RequestContext
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.context import ExecutionSource
from tests.agent.pr5_semantic_fakes import StaticPlanningGateway, proposal, step


def _runtime(*, prepared_write=None):
    request = RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=frozenset({"RESIDENT"}),
        bound_house_ids=frozenset(),
        current_house_id=None,
        request_id="request-1",
        execution_source=ExecutionSource.AGENT,
    )
    return RuntimeContext.from_request_context(
        request,
        conversation_id="conversation-1",
        prepared_write=prepared_write,
    )


def _state(text):
    return AgentState(conversation_id="conversation-1", slots={"user_text": text})


def test_planner_builds_conditional_repair_plan_instead_of_blind_create():
    planner = SupervisorPlanner(
        StaticPlanningGateway(
            proposal(
                step("repair-read", "repair", "repair_list", "查找等价活跃报修"),
                step(
                    "repair-create",
                    "repair",
                    "repair_create",
                    "不存在等价工单时提交报修",
                    parameters={"description": "厨房漏水", "location": "厨房"},
                    dependencies=("repair-read",),
                    condition={
                        "kind": "no-equivalent-active-repair",
                        "semantic_goal": "没有等价的活跃厨房漏水报修",
                    },
                ),
            )
        )
    )

    plan = planner.create_plan(
        _state("厨房一直漏水，看看之前有没有报修，如果没有帮我报一个。"), _runtime()
    )

    assert plan.objective_classification is ObjectiveClassification.SINGLE_DOMAIN
    assert [step.capability for step in plan.steps] == ["repair_list", "repair_create"]
    assert plan.steps[1].condition == "if_no_equivalent_active_repair"
    assert plan.steps[1].dependencies == (plan.steps[0].step_id,)


def test_planner_builds_minimal_repair_billing_multi_domain_plan():
    planner = SupervisorPlanner(
        StaticPlanningGateway(
            proposal(
                step("repair-read", "repair", "repair_list", "查询报修进度"),
                step("billing-read", "billing", "billing_query", "查询本期费用"),
            )
        )
    )

    plan = planner.create_plan(
        _state("查一下我的报修进度，顺便看看这个月物业费有没有欠。"), _runtime()
    )

    assert plan.objective_classification is ObjectiveClassification.MULTI_DOMAIN
    assert [step.specialist for step in plan.steps] == [
        SpecialistName.REPAIR,
        SpecialistName.BILLING,
    ]
    assert [step.capability for step in plan.steps] == ["repair_list", "billing_query"]


def test_planner_builds_conditional_inspection_to_announcement_plan():
    semantic = proposal(
        step("inspection-read", "inspection", "inspection_list", "核验电梯巡检发现"),
        step(
            "announcement-draft",
            "announcement",
            "announcement_draft",
            "在发现相关电梯问题时准备公告",
            dependencies=("inspection-read",),
            condition={
                "kind": "relevant-inspection-issue",
                "semantic_goal": "巡检发现与电梯故障相关的问题",
            },
        ),
    )
    plan = SupervisorPlanner(StaticPlanningGateway(semantic)).create_plan(
        _state("看看电梯故障有没有巡检发现，如果真的有问题，准备一份业主公告。"),
        _runtime(),
    )

    assert [step.capability for step in plan.steps] == [
        "inspection_list",
        "announcement_draft",
    ]
    assert plan.steps[1].condition == "if_relevant_inspection_issue"


def test_general_help_has_no_unnecessary_capability_step():
    plan = SupervisorPlanner(
        StaticPlanningGateway(proposal(classification="general-help"))
    ).create_plan(_state("你好，你能做什么？"), _runtime())
    assert plan.objective_classification is ObjectiveClassification.GENERAL_HELP
    assert plan.steps == ()


def test_malformed_gateway_proposal_fails_closed_without_lexical_reconstruction():
    class MalformedGateway:
        def propose_plan(self, *_args, **_kwargs):
            return {"intent": "REPAIR", "steps": "not-a-schema"}

    plan = SupervisorPlanner(MalformedGateway()).create_plan(_state("我要报修"), _runtime())

    assert plan.objective_classification is ObjectiveClassification.UNCERTAIN
    assert plan.steps == ()


def test_contextual_repair_followup_uses_history_and_prior_semantic_slots():
    state = _state("那就帮我处理。")
    state.messages = [
        {"role": "user", "content": "那个报修怎么样了？"},
        {"role": "assistant", "content": "没有找到活跃报修。"},
        {"role": "user", "content": "那就帮我处理。"},
    ]
    state.slots.update(description="厨房漏水", location="厨房")

    semantic = proposal(
        step(
            "repair-create",
            "repair",
            "repair_create",
            "提交上下文所指的厨房漏水报修",
            parameters={"description": "厨房漏水", "location": "厨房"},
        )
    )
    plan = SupervisorPlanner(StaticPlanningGateway(semantic)).create_plan(state, _runtime())

    assert [step.capability for step in plan.steps] == ["repair_create"]
    assert plan.steps[0].parameters["location"] == "厨房"


def test_explicit_inspection_and_announcement_actions_map_to_canonical_capabilities():
    inspection = _state("上报安防事件")
    inspection.slots.update(
        action="report_event",
        event_type="FIRE_HAZARD",
        risk_level="LOW_RISK",
        location="车库",
        description="发现烟雾和明火",
    )
    announcement = _state("立即发布公告")
    announcement.slots.update(action="publish", announcement_id=str(uuid4()), expected_version=1)

    inspection_plan = SupervisorPlanner(
        StaticPlanningGateway(
            proposal(
                step(
                    "security-write",
                    "inspection",
                    "security_event_create",
                    "上报安防事件",
                    parameters=dict(inspection.slots),
                )
            )
        )
    ).create_plan(inspection, _runtime())
    announcement_plan = SupervisorPlanner(
        StaticPlanningGateway(
            proposal(
                step(
                    "announcement-publish",
                    "announcement",
                    "announce_publish",
                    "发布现有公告",
                    parameters=dict(announcement.slots),
                )
            )
        )
    ).create_plan(announcement, _runtime())

    assert inspection_plan.steps[0].capability == "security_event_create"
    assert announcement_plan.steps[0].capability == "announce_publish"


def test_deepseek_knowledge_search_proposal_survives_normalization_and_validation():
    def handler(_request):
        content = json.dumps(
            {
                "objective_classification": "single-domain",
                "steps": [
                    {
                        "step_id": "knowledge-search",
                        "goal": "查询社区物业服务电话",
                        "domain": "announcement",
                        "specialist": "AnnouncementSpecialist",
                        "capability": "community_knowledge_search",
                        "parameters": {"query": "物业服务电话", "limit": 5},
                        "dependencies": [],
                        "condition": None,
                    }
                ],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    gateway = DeepSeekModelGateway(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-test",
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    plan = SupervisorPlanner(gateway).create_plan(_state("物业电话是多少？"), _runtime())

    assert plan.objective_classification is ObjectiveClassification.SINGLE_DOMAIN
    assert plan.steps[0].domain == "announcement"
    assert plan.steps[0].specialist is SpecialistName.ANNOUNCEMENT
    assert plan.steps[0].capability == "community_knowledge_search"


def test_specialist_allowlists_are_exact_registry_domain_views():
    executor = _executor({})
    specialists = (
        RepairSpecialist(executor),
        BillingSpecialist(executor),
        AnnouncementSpecialist(executor),
        InspectionSpecialist(executor),
    )
    registry = default_capability_registry()
    for specialist in specialists:
        expected = frozenset(
            spec.name for spec in registry.inventory() if spec.domain == specialist.domain
        )
        assert specialist.allowlist == expected


def test_billing_specialist_executes_only_billing_query_and_interprets_result():
    executor = _executor(
        {
            "billing_query": lambda _request, _runtime: {
                "query_type": "list",
                "period": "2026-08",
                "count": 0,
                "items": (),
            }
        }
    )
    step = PlanStep(
        "billing-1",
        "billing",
        SpecialistName.BILLING,
        "query current bill",
        capability="billing_query",
        parameters={"query_type": "list", "period": "2026-08"},
    )

    result = BillingSpecialist(executor).invoke(step, _state("物业费"), _runtime(), ())

    assert result.outcome is SpecialistOutcome.SUCCESS
    assert result.capability == "billing_query"
    assert result.data["count"] == 0


def test_announcement_specialist_executes_knowledge_search_through_executor_allowlist():
    calls = []

    def search(request, _runtime_context):
        calls.append((request.query, request.limit))
        return {"data": {"count": 1, "items": [{"title": "物业服务电话"}]}}

    step_value = PlanStep(
        "knowledge-1",
        "announcement",
        SpecialistName.ANNOUNCEMENT,
        "search community knowledge",
        capability="community_knowledge_search",
        parameters={"query": "物业服务电话", "limit": 5},
    )
    specialist = AnnouncementSpecialist(_executor({"community_knowledge_search": search}))

    result = specialist.invoke(step_value, _state("物业电话是多少？"), _runtime(), ())

    assert "community_knowledge_search" in specialist.allowlist
    assert calls == [("物业服务电话", 5)]
    assert result.outcome is SpecialistOutcome.SUCCESS
    assert result.capability == "community_knowledge_search"
    assert result.data["data"]["count"] == 1


def test_inspection_not_found_requests_materially_different_replan():
    def missing(_request, _runtime):
        raise CapabilityDomainError("TASK_NOT_FOUND", "未找到巡检任务")

    step = PlanStep(
        "inspection-1",
        "inspection",
        SpecialistName.INSPECTION,
        "get described task",
        capability="inspection_get_task",
        parameters={"task_id": str(uuid4())},
    )
    result = InspectionSpecialist(_executor({"inspection_get_task": missing})).invoke(
        step, _state("昨天车库那个异常"), _runtime(), ()
    )

    assert result.outcome is SpecialistOutcome.REPLAN
    assert result.reason_code == "TASK_NOT_FOUND"
    assert result.data["replacement_capability"] == "inspection_list"
    assert result.data["replacement_parameters"]["target"] == "task"


def test_inspection_event_not_found_replans_to_event_discovery_with_filters():
    def missing(_request, _runtime):
        raise CapabilityDomainError("EVENT_NOT_FOUND", "未找到安防事件")

    step_value = PlanStep(
        "inspection-event",
        "inspection",
        SpecialistName.INSPECTION,
        "get described event",
        capability="inspection_get_event",
        parameters={"event_id": str(uuid4()), "risk_levels": ("HIGH_RISK",), "limit": 5},
    )
    result = InspectionSpecialist(_executor({"inspection_get_event": missing})).invoke(
        step_value, _state("查询所指事件"), _runtime(), ()
    )

    assert result.outcome is SpecialistOutcome.REPLAN
    assert result.reason_code == "EVENT_NOT_FOUND"
    assert result.data["replacement_capability"] == "inspection_list"
    assert result.data["replacement_parameters"] == {
        "target": "event",
        "limit": 5,
        "risk_levels": ("HIGH_RISK",),
    }


def test_non_null_prepared_write_does_not_confirm_a_different_exact_action():
    calls = []
    executor = _executor(
        {
            "repair_create": lambda request, _runtime: (
                calls.append(request)
                or {
                    "work_order": {
                        "id": str(uuid4()),
                        "status": "PENDING",
                        "category": "OTHER",
                        "urgency": "NORMAL",
                    },
                    "idempotency_key": "key",
                }
            )
        }
    )
    parameters = {"description": "漏水", "location": "厨房", "urgency": "NORMAL"}
    step = PlanStep(
        "write-b",
        "repair",
        SpecialistName.REPAIR,
        "create repair",
        capability="repair_create",
        parameters=parameters,
    )
    wrong = PreparedWrite(
        "token-a",
        "key-a",
        "approval-a",
        capability="billing_consult",
        params_hash="other-hash",
        plan_id="plan-1",
        plan_step_id="write-a",
    )
    state = _state("提交报修")
    state.plan = SimpleNamespace(plan_id="plan-1")

    result = RepairSpecialist(executor).invoke(step, state, _runtime(prepared_write=wrong), ())

    assert result.outcome is SpecialistOutcome.HITL_REQUIRED
    assert calls == []
    assert result.data["params_hash"] == canonical_hash(parameters)


def _executor(adapters):
    return CapabilityExecutor(
        default_capability_registry(),
        default_capability_policy(),
        adapters,
    )
