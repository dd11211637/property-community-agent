"""确定性首轮信号提取 —— repair / inspection / 公告的纯函数修正。

这些规则只依赖用户文本 + 上轮 ``GraphState``，不依赖模型输出，
因此可在任何请求路径（HTTP / 内部 ReAct / 后台扫描）上复用，避免
智能体会话被分类器误判。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from property_agent.agent.policies import Intent
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import (
    DomainWorkingState,
    EmptyWorkingState,
    InspectionEventWorkingState,
    InspectionTaskWorkingState,
    RepairWorkingState,
    domain_from_legacy,
    project_domain_to_legacy_slots,
)

# 巡检 / 安防写信号
_INSPECTION_ACTION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("report_event", ("上报事件", "报告事件", "安防事件上报")),
    ("submit_disposal", ("提交处置", "处置结果", "完成处置")),
    ("start_task", ("开始巡检", "开始任务", "执行巡检")),
    ("add_record", ("追加记录", "添加记录", "补充记录")),
    ("submit_records", ("提交记录", "结束巡检")),
    ("create", ("创建巡检", "新建巡检", "安排巡检", "开展巡检", "进行巡检")),
    ("query", ("查询任务", "查询事件", "巡检任务", "巡检记录", "都完成", "完成了吗")),
)

_INSPECTION_SLOT_GROUPS = {
    "task_query": {"statuses", "assigned_to_me", "limit", "task_id"},
    "task_write": {
        "task_id",
        "expected_version",
        "title",
        "description",
        "point",
        "route_points",
        "note",
        "record_type",
    },
    "event_query": {"event_id", "statuses", "risk_levels", "assigned_to_me", "limit"},
    "event_write": {
        "event_id",
        "expected_version",
        "event_type",
        "risk_level",
        "location",
        "description",
        "note",
        "task_id",
    },
}

_FIRST_TURN_INSPECTION_MARKERS = ("巡检", "消防通道", "安防", "巡检发现", "检查发现")
_INSPECTION_WRITE_MARKERS = ("上报异常", "上报事件", "上报", "报告异常", "异常上报")

# 续接判定：短改口、修正、回归等
_CONTEXTUAL_MARKERS = (
    "那",
    "那么",
    "刚才",
    "刚刚",
    "上个月",
    "上上个月",
    "本月",
    "这个月",
    "不是",
    "改成",
    "采用",
    "保存草稿",
    "立即发布",
    "确认发布",
    "定时发布",
    "预约发布",
    "换成",
    "那个",
    "这个",
    "重试",
    "再试",
)


def explicit_inspection_action(text: str) -> str | None:
    """对用户文本做确定性巡检动作归类（不依赖模型）。"""
    if any(marker in text for marker in ("了吗", "了没", "进度", "查询", "查看")):
        if any(marker in text for marker in ("巡检", "任务", "事件")):
            return "query"
    for action, markers in _INSPECTION_ACTION_MARKERS:
        if any(marker in text for marker in markers):
            if (
                action == "create"
                and "进行巡检" in text
                and "对" not in text
                and "我要" not in text
            ):
                continue
            return action
    return None


def first_turn_inspection_signal(user_text: str, roles: tuple[str, ...]) -> dict[str, str]:
    """首轮确定性巡检信号：命中则锁定 INSPECTION 并走安防事件上报。

    仅靠 LLM 分类会把"消防通道堵塞上报"这类公共区域安防问题误判成住户报修；
    这里对巡检/安防写信号做确定性兜底（安全优先于模型判断）。``roles`` 只取
    可信请求上下文中的角色，不允许来自模型输出。
    """
    text = str(user_text or "")
    if any(marker in text for marker in _FIRST_TURN_INSPECTION_MARKERS) and any(
        marker in text for marker in _INSPECTION_WRITE_MARKERS
    ):
        return {"action": "report_event"}
    if "SECURITY_GUARD" in roles and any(
        marker in text for marker in ("堵塞", "异常", "可疑", "上报")
    ):
        return {"action": "report_event"}
    return {}


def inspection_group(action: str, text: str) -> str:
    """根据动作 + 文本，把巡检 slot 集合归类为 query / event_write / task_write。"""
    if action == "query":
        return "event_query" if "事件" in text or "安防" in text else "task_query"
    if action in {"report_event", "submit_disposal"}:
        return "event_write"
    return "task_write"


def looks_contextual(text: str) -> bool:
    """短改口 / 回归等"上下文式 followup"判定（≤24 字符 + 命中续接标记）。"""
    compact = text.strip()
    return bool(compact) and (
        len(compact) <= 24 and any(marker in compact for marker in _CONTEXTUAL_MARKERS)
    )


def explicit_repair_corrections(text: str) -> dict[str, str]:
    """Extract user-authored corrections without asking a model to mutate trusted state."""
    if not any(marker in text for marker in ("不是", "改成", "换成")):
        return {}
    corrections: dict[str, str] = {}
    locations = ("厨房", "卫生间", "客厅", "卧室", "阳台", "玄关", "楼道", "车库")
    mentioned_locations = [value for value in locations if value in text]
    if mentioned_locations:
        corrections["location"] = mentioned_locations[-1]
    symptom_cues = (
        "漏电",
        "电路",
        "电线",
        "插座",
        "停电",
        "跳闸",
        "灯",
        "照明",
        "开关",
        "漏水",
        "水管",
        "下水",
        "水龙头",
        "马桶",
        "堵塞",
        "电梯",
        "困梯",
    )
    if any(cue in text for cue in symptom_cues):
        corrections["description"] = text.strip()
    return corrections


def explicit_inspection_corrections(text: str, previous: GraphState | None) -> dict[str, str]:
    """Map a user correction to the active inspection field, never to identity fields."""
    if previous is None or not isinstance(
        previous.domain, (InspectionTaskWorkingState, InspectionEventWorkingState)
    ):
        return {}
    if not any(marker in text for marker in ("不是", "改成", "换成")):
        return {}
    locations = (
        "小区出入口",
        "楼栋大厅",
        "大厅",
        "消防通道",
        "地下车库",
        "车库",
        "公共设备间",
        "设备间",
    )
    mentioned = [value for value in locations if value in text]
    if not mentioned:
        return {}
    value = max(mentioned, key=len)
    action = str(previous.domain.action or "")
    field = "location" if action in {"report_event", "create_event", "event_create"} else "point"
    return {field: value}


def resolve_repair_followup(
    previous: GraphState | None,
    user_text: str,
    explicit_corrections: dict[str, str],
) -> tuple[dict[str, Any], str | None]:
    """若上一轮已有活跃工单且用户改口/回归，直接说明不重复建单。"""
    if previous is None or not isinstance(previous.domain, RepairWorkingState):
        return {}, None
    repair = previous.domain
    if not repair.work_order_id:
        return {}, None
    correction_or_return = any(
        marker in user_text for marker in ("不是", "改成", "换成", "回到", "刚才")
    )
    if not correction_or_return:
        return {}, None
    business_no = str(repair.work_order_id)
    location = explicit_corrections.get("location") or repair.location or ""
    description = explicit_corrections.get("description") or repair.description or ""
    followup = {
        "work_order_id": business_no,
        "location": location,
        "description": description,
    }
    message = (
        f"您的报修工单 {business_no} 已提交（位置：{location}），"
        "正在处理中；如需正式修改地点或补充说明，请致电物业。"
    )
    return followup, message


@dataclass(slots=True)
class ContinuationState:
    """Runner 内私有槽位续接状态（仅供 ``runner._build_continuation`` 使用）。"""

    previous_domain: DomainWorkingState
    legacy_projection: dict[str, Any]
    previous_messages: list[dict[str, Any]]
    previous_intent: str | None
    single_slot_reply: dict[str, Any]
    slot_continuation: bool
    contextual_followup: bool
    continuing: bool


def build_initial_state(
    *,
    conversation_id: str,
    context: Any,
    current_house_id: Any,
    user_text: str,
    slots: dict[str, Any] | None,
    inspection_override: dict[str, str],
    explicit_corrections: dict[str, str],
    continuation: ContinuationState,
    active_draft: dict[str, Any] | None,
    announcement_followup: Any,
    repair_followup: dict[str, Any],
) -> GraphState:
    intent = Intent.INSPECTION.value if inspection_override else continuation.previous_intent
    compatibility_slots = {
        **continuation.legacy_projection,
        **explicit_corrections,
        **continuation.single_slot_reply,
        "_user_corrected_fields": sorted(
            set(explicit_corrections) | set((announcement_followup.slot_updates or {}).keys())
        ),
        "_active_announcement_draft": active_draft,
        "user_text": user_text,
        **repair_followup,
        **(slots or {}),
    }
    domain = domain_from_legacy(intent, compatibility_slots) if intent else EmptyWorkingState()
    return GraphState(
        conversation_id=conversation_id,
        actor_id=context.actor_id,
        community_id=context.community_id,
        current_house_id=current_house_id,
        intent=intent,
        domain=domain,
        slots=project_domain_to_legacy_slots(domain, compatibility_slots),
        messages=continuation.previous_messages,
        _continuation=continuation.continuing,
        _contextual_followup=continuation.contextual_followup,
    )


__all__ = [
    "ContinuationState",
    "build_initial_state",
    "explicit_inspection_action",
    "first_turn_inspection_signal",
    "inspection_group",
    "looks_contextual",
    "explicit_repair_corrections",
    "explicit_inspection_corrections",
    "resolve_repair_followup",
]
