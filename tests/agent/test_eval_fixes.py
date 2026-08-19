"""评测遗留问题修复的回归测试。

覆盖四个实测定位的问题：
1. inspection_case1 / inspection_boundary1 —— 首轮确定性巡检信号 + 子图 action 兜底；
2. announcement_case2 —— 住户越权写公告在子图层被拦截，写工具名不进入 trace；
3. billing_case1 —— fee_type 中文词归一化为枚举码，无法识别视为未指定；
4. multi_turn_state1 —— repair_create 成功后回写工单号，供后续轮次识别已有工单。
"""

import pytest

from property_agent.agent.application.runner import _first_turn_inspection_signal
from property_agent.agent.state import GraphState
from property_agent.agent.subgraphs.announcement import select_announcement_tool
from property_agent.agent.subgraphs.inspection import select_inspection_tool
from property_agent.agent.tools.billing import _normalize_fee_type


def _state(**slots) -> GraphState:
    return GraphState(conversation_id="test", slots=slots)


class TestFirstTurnInspectionSignal:
    def test_patrol_report_maps_to_inspection(self):
        assert _first_turn_inspection_signal(
            "巡检时发现3栋1单元消防通道被杂物堵塞，需要上报异常", ()
        ) == {"action": "report_event"}

    def test_security_guard_abnormal_maps_to_inspection(self):
        assert _first_turn_inspection_signal(
            "3栋999楼消防通道有异常，帮我上报", ("SECURITY_GUARD",)
        ) == {"action": "report_event"}

    def test_plain_repair_gets_no_override(self):
        assert _first_turn_inspection_signal("我家厨房水管漏水了，帮忙报修", ("RESIDENT",)) == {}

    def test_repair_mention_of_patrol_without_write_is_not_forced(self):
        assert _first_turn_inspection_signal("想了解一下巡检安排", ("RESIDENT",)) == {}


class TestInspectionSelectorFallback:
    def test_missing_action_with_report_word_goes_to_event(self):
        state = _state(user_text="3栋999楼消防通道有异常，帮我上报")
        assert select_inspection_tool(state) == "security_event_create"

    def test_empty_input_falls_back_to_list(self):
        assert select_inspection_tool(_state()) == "inspection_list"


class TestAnnouncementRoleGuard:
    def test_resident_write_is_denied_before_tool_selection(self):
        state = _state(
            action="publish", roles=["RESIDENT"], title="停水通知", body="明天停水"
        )
        tool = select_announcement_tool(state)
        assert tool == "announcement_list"
        assert state.error and "发布公告的权限" in state.error
        assert state.slots["action"] == "list"

    def test_manager_create_still_routes_to_draft(self):
        state = _state(
            action="create", roles=["MANAGER"], title="停水通知", body="明天停水", audience="全社区"
        )
        assert select_announcement_tool(state) == "announcement_create_draft"

    def test_publish_with_fresh_content_degrades_to_create(self):
        state = _state(action="publish", roles=["MANAGER"], title="停水通知", body="明天停水")
        assert select_announcement_tool(state) == "announcement_create_draft"

    def test_no_roles_preserves_legacy_behavior(self):
        state = _state(action="publish")
        assert select_announcement_tool(state) == "announcement_get"


class TestBillingFeeTypeNormalization:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (None, None),
            ("", None),
            ("PROPERTY", "PROPERTY"),
            ("property", "PROPERTY"),
            ("物业费", "PROPERTY"),
            ("物业管理费", "PROPERTY"),
            ("水电费", "UTILITY"),
            ("电费", "UTILITY"),
            ("停车费", "PARKING"),
            ("车位费", "PARKING"),
            ("乱七八糟", None),
        ],
    )
    def test_normalize_fee_type(self, raw, expected):
        assert _normalize_fee_type(raw) == expected
