import pytest

from property_agent.agent.announcement_actions import (
    AnnouncementAgentAction,
    normalize_announcement_action,
    normalize_announcement_audience,
    resolve_announcement_followup,
)
from property_agent.agent.model_gateway import (
    DeterministicModelGateway,
    ModelGatewayError,
)


def test_action_contract_normalizes_model_synonyms():
    assert normalize_announcement_action("edit") == AnnouncementAgentAction.REVISE
    assert normalize_announcement_action("schedule_publish") == AnnouncementAgentAction.SCHEDULE
    assert normalize_announcement_action("unknown_free_form") is None


def test_modify_announcement_is_not_misclassified_as_repair():
    result = DeterministicModelGateway().analyze("修改公告，语气正式一点")
    assert result.intent == "ANNOUNCEMENT"


def test_implicit_revision_requires_an_active_draft():
    assert (
        resolve_announcement_followup("语气正式一点", has_active_draft=True).action
        == AnnouncementAgentAction.REVISE
    )
    assert resolve_announcement_followup("语气正式一点", has_active_draft=False).action is None


def test_modify_reason_is_resolved_as_active_draft_revision():
    followup = resolve_announcement_followup(
        "修改原因，原因是洪水引发的供水设施损坏，需要检修",
        has_active_draft=True,
    )
    assert followup.action == AnnouncementAgentAction.REVISE
    assert followup.instruction == "修改原因，原因是洪水引发的供水设施损坏，需要检修"


def test_multi_field_revision_extracts_structured_audience_update():
    followup = resolve_announcement_followup(
        "标题简短一点，原因改成管网损坏，受众改为1栋",
        has_active_draft=True,
    )
    assert followup.action == AnnouncementAgentAction.REVISE
    assert followup.instruction.startswith("标题简短一点")
    assert followup.slot_updates == {"audience": {"building_ids": ["1栋"]}}


@pytest.mark.parametrize("value", ["1栋住户", "一栋住户", "只有一栋"])
def test_display_audience_is_normalized_to_business_contract(value):
    assert normalize_announcement_audience(value) == {"building_ids": ["1栋"]}


def test_natural_audience_revision_is_structured_before_confirmation():
    followup = resolve_announcement_followup("修改受众只有一栋住户", has_active_draft=True)

    assert followup.action == AnnouncementAgentAction.REVISE
    assert followup.slot_updates == {"audience": {"building_ids": ["1栋"]}}


def test_adopt_this_draft_wording_routes_to_create():
    followup = resolve_announcement_followup("采用该稿件", has_active_draft=False)

    assert followup.action == AnnouncementAgentAction.CREATE


def test_missing_specific_time_is_not_invented_as_revision_instruction():
    followup = resolve_announcement_followup("明天要有具体时间", has_active_draft=True)
    assert followup.action == AnnouncementAgentAction.REVISE
    assert followup.instruction is None
    assert followup.detail_kind == "event_time"


def test_keyword_fallback_never_pretends_it_revised_prose():
    with pytest.raises(ModelGatewayError, match="暂不可用"):
        DeterministicModelGateway().revise_announcement(
            draft={"title": "通知", "body": "原稿", "category": "GENERAL"},
            audience={},
            instruction="正式一点",
        )
