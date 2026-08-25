"""Holdout and metamorphic checks for model-first PR5 planning."""

from property_agent.agent.model_contracts import (
    ModelAnalysis,
    ModelGatewayError,
    UnavailableModelGateway,
)
from property_agent.agent.orchestration import ObjectiveClassification, SpecialistName
from property_agent.agent.planning import SupervisorPlanner
from tests.agent.pr5_semantic_fakes import StaticPlanningGateway, proposal, step
from tests.agent.test_pr5_planning_and_specialists import _runtime, _state


def _capabilities(text, semantic, *, history=None):
    state = _state(text)
    state.messages = list(history or ()) + [{"role": "user", "content": text}]
    gateway = StaticPlanningGateway(semantic)
    plan = SupervisorPlanner(gateway).create_plan(state, _runtime())
    return plan, gateway


def test_billing_paraphrases_are_semantically_invariant_after_provider_proposal():
    semantic = proposal(
        step(
            "billing-read",
            "billing",
            "billing_query",
            "核验最近一期是否存在未结清费用",
            parameters={"query_type": "list"},
        )
    )

    canonical, _ = _capabilities("这个月物业费有没有欠？", semantic)
    holdout, _ = _capabilities("最近这期是不是还有钱没结清？", semantic)

    assert [item.capability for item in canonical.steps] == ["billing_query"]
    assert [item.capability for item in holdout.steps] == ["billing_query"]
    assert canonical.steps[0].goal == holdout.steps[0].goal


def test_multi_domain_paraphrase_without_canonical_domain_nouns_preserves_both_goals():
    semantic = proposal(
        step("repair-read", "repair", "repair_list", "查询水槽问题处理进度"),
        step("billing-read", "billing", "billing_query", "核验最近一期未结清费用"),
    )

    plan, _ = _capabilities(
        "看看水槽下面那个问题处理到哪了，另外最近一期还有钱没结清吗？", semantic
    )

    assert plan.objective_classification is ObjectiveClassification.MULTI_DOMAIN
    assert [item.specialist for item in plan.steps] == [
        SpecialistName.REPAIR,
        SpecialistName.BILLING,
    ]


def test_distractor_and_irrelevant_detail_do_not_add_specialists():
    billing_only = proposal(step("billing-read", "billing", "billing_query", "查询未结清费用"))
    distractor, _ = _capabilities(
        "公告和维修都与这次无关，我只想知道最近一期还有没有钱没结清。", billing_only
    )
    verbose, _ = _capabilities(
        "昨天看到维修人员贴公告，不过那只是背景；只查我最近一期是否结清。", billing_only
    )

    assert [item.specialist for item in distractor.steps] == [SpecialistName.BILLING]
    assert [item.specialist for item in verbose.steps] == [SpecialistName.BILLING]


def test_negation_preserves_read_goal_and_excludes_repair_create():
    read_only = proposal(step("repair-read", "repair", "repair_list", "只查历史记录"))

    plan, _ = _capabilities("不要帮我报修，只查之前的记录。", read_only)

    assert [item.capability for item in plan.steps] == ["repair_list"]


def test_conditional_paraphrase_preserves_dependency_without_trigger_words():
    semantic = proposal(
        step("inspection-read", "inspection", "inspection_list", "核验电梯巡检发现"),
        step(
            "announcement-draft",
            "announcement",
            "announcement_draft",
            "相关问题成立后准备公告",
            parameters={"topic": "电梯检修", "audience": {}, "requirements": "安全提示"},
            dependencies=("inspection-read",),
            condition={
                "kind": "relevant-inspection-issue",
                "semantic_goal": "巡检证据确认电梯异常",
            },
        ),
    )

    plan, _ = _capabilities("先核验电梯记录，能坐实异常再准备住户说明。", semantic)

    assert plan.steps[1].dependencies == ("inspection-read",)
    assert plan.steps[1].condition == "if_relevant_inspection_issue"


def test_context_reference_is_sent_to_semantic_provider_without_reconstructing_keywords():
    semantic = proposal(
        step(
            "repair-create",
            "repair",
            "repair_create",
            "提交上一轮明确的厨房漏水报修",
            parameters={"description": "厨房漏水", "location": "厨房"},
        )
    )
    history = [
        {"role": "user", "content": "厨房水槽下面漏水"},
        {"role": "assistant", "content": "没有找到活跃记录。"},
    ]

    plan, gateway = _capabilities("那就处理吧。", semantic, history=history)

    assert [item.capability for item in plan.steps] == ["repair_create"]
    assert gateway.requests[0]["history"][-2:] == [
        history[-1],
        {"role": "user", "content": "那就处理吧。"},
    ]


def test_unavailable_semantic_provider_does_not_lexically_fake_complex_plan():
    plan = SupervisorPlanner(UnavailableModelGateway()).create_plan(
        _state("查两个领域并按结果决定后续动作"), _runtime()
    )

    assert plan.objective_classification is ObjectiveClassification.UNCERTAIN
    assert plan.steps == ()


def test_failed_semantic_provider_does_not_fall_back_to_lexical_write_plan():
    class FailedSemanticProvider:
        def propose_plan(self, *_args, **_kwargs):
            raise ModelGatewayError("provider unavailable")

        def analyze_with_context(self, *_args, **_kwargs):
            return ModelAnalysis("REPAIR", 1.0, {"action": "create"}, "lexical")

    plan = SupervisorPlanner(FailedSemanticProvider()).create_plan(
        _state("复杂条件任务"), _runtime()
    )

    assert plan.objective_classification is ObjectiveClassification.UNCERTAIN
    assert plan.steps == ()


def test_unknown_provider_capability_fails_closed_at_plan_validator():
    unsafe = proposal(step("billing-read", "billing", "delete_everything", "查询费用"))

    plan, _ = _capabilities("查询费用", unsafe)

    assert plan.objective_classification is ObjectiveClassification.UNCERTAIN
    assert plan.steps == ()


def test_provider_cannot_place_server_owned_scope_in_executable_parameters():
    unsafe = proposal(
        step(
            "billing-read",
            "billing",
            "billing_query",
            "查询费用",
            parameters={"community_id": "model-selected-community"},
        )
    )

    plan, _ = _capabilities("查询费用", unsafe)

    assert plan.objective_classification is ObjectiveClassification.UNCERTAIN
    assert plan.steps == ()
