"""Deterministic intent routing and non-authoritative slot extraction."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from property_agent.agent.announcement_time import (
    resolve_announcement_time_slots,
    trusted_business_date,
)
from property_agent.agent.model_contracts import ModelAnalysis, ModelGatewayError
from property_agent.agent.policies import Intent
from property_agent.repair.domain.classification import classify_repair_category

_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _business_today() -> date:
    return datetime.now(_BUSINESS_TIMEZONE).date()


def _shift_month(value: date, offset: int) -> str:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return f"{year:04d}-{zero_based_month + 1:02d}"


def _deterministic_billing_slots(text: str, today: date) -> dict[str, Any]:
    """Extract trusted billing list filters, including relative Chinese months."""

    text = text or ""
    slots: dict[str, Any] = {}
    absolute = re.search(r"(?P<year>20\d{2})\s*(?:年|-|/)\s*(?P<month>1[0-2]|0?[1-9])\s*月?", text)
    if absolute:
        slots["period"] = f"{int(absolute.group('year')):04d}-{int(absolute.group('month')):02d}"
    elif any(marker in text for marker in ("本月", "这个月", "当月", "本月份")):
        slots["period"] = _shift_month(today, 0)
    elif any(marker in text for marker in ("上上月", "上上个月")):
        slots["period"] = _shift_month(today, -2)
    elif any(marker in text for marker in ("上月", "上个月")):
        slots["period"] = _shift_month(today, -1)
    if "period" in slots:
        slots["query_type"] = "list"
    return slots


def _deterministic_repair_slots(text: str) -> dict[str, Any]:
    """Extract observable repair facts; category remains application-derived."""

    text = (text or "").strip()
    slots: dict[str, Any] = {}
    business_no = re.search(r"WX-[A-Z0-9]+(?:-[A-Z0-9]+)*", text, re.IGNORECASE)
    if business_no:
        slots.update(action="query", work_order_id=business_no.group(0).upper())
        return slots
    if any(
        marker in text
        for marker in (
            "查询工单",
            "查看工单",
            "查工单",
            "工单进度",
            "报修进度",
            "维修进度",
            "报修记录",
            "维修记录",
        )
    ):
        slots["action"] = "query"
        return slots
    create_markers = ("报修", "维修", "坏了", "故障", "漏水", "漏电", "破损", "堵塞")
    if any(marker in text for marker in create_markers):
        slots["action"] = "create"
    locations = (
        "厨房",
        "卫生间",
        "客厅",
        "卧室",
        "阳台",
        "玄关",
        "楼道",
        "地下车库",
        "车库",
    )
    location = next((value for value in locations if value in text), None)
    if location:
        slots["location"] = location
    generic = {"我要报修", "需要报修", "申请报修", "报修", "我要保修", "帮我报修"}
    symptom_cues = (
        "坏了",
        "损坏",
        "漏水",
        "渗水",
        "漏电",
        "堵塞",
        "停电",
        "跳闸",
        "故障",
        "破损",
        "异响",
        "无法",
    )
    if text not in generic and any(cue in text for cue in symptom_cues):
        slots["description"] = text
        slots["category"] = classify_repair_category(text).value
    return slots


def _deterministic_announcement_slots(text: str, today: date) -> dict[str, Any]:
    drafting = any(
        marker in text for marker in ("帮我写公告", "写公告", "起草公告", "润色公告", "公告草稿")
    )
    creating = any(
        marker in text
        for marker in (
            "创建草稿",
            "保存草稿",
            "采用这个稿件",
            "采用这份稿件",
            "使用这个稿件",
            "使用这份稿件",
        )
    )
    scheduling = any(marker in text for marker in ("定时发布", "预约发布", "到点发布"))
    publishing = any(marker in text for marker in ("立即发布", "现在发布", "确认发布"))
    querying = any(marker in text for marker in ("查询公告", "查看公告", "公告列表", "公告详情"))
    action = None
    for matched, candidate in (
        (querying, "list"),
        (drafting, "draft"),
        (creating, "create"),
        (publishing, "publish"),
        (scheduling, "schedule"),
    ):
        if matched:
            action = candidate
    slots: dict[str, Any] = {"action": action} if action else {}
    if any(marker in text for marker in ("停水", "供水")):
        slots["topic"] = "WATER_OUTAGE"
    elif any(marker in text for marker in ("停电", "供电")):
        slots["topic"] = "POWER_OUTAGE"
    slots.update(resolve_announcement_time_slots(text, today))
    building_match = re.search(r"(?P<buildings>\d+(?:\s*[,，、]\s*\d+)*)\s*栋", text)
    if building_match:
        slots["audience"] = {
            "building_ids": [
                f"{value.strip()}栋"
                for value in re.split(r"[,，、]", building_match.group("buildings"))
            ]
        }
    elif "全社区" in text or "所有住户" in text:
        slots["audience"] = {}
    topic_match = re.search(r"(?:主题|关于|内容)[是为：:\s]*(?P<topic>[^，。；;]{2,40})", text)
    if topic_match:
        slots["topic"] = topic_match.group("topic").strip()
    return slots


def _deterministic_inspection_slots(text: str) -> dict[str, Any]:
    text = text or ""
    slots: dict[str, Any] = {}
    task_query = any(
        marker in text
        for marker in (
            "查询任务",
            "查看任务",
            "巡检任务",
            "巡检记录",
            "巡检进度",
            "都完成",
            "完成了吗",
            "完成了没",
        )
    )
    task_create = (
        any(marker in text for marker in ("创建巡检", "新建巡检", "安排巡检", "开展巡检"))
        or ("我要" in text and "巡检" in text)
        or ("对" in text and "进行巡检" in text)
    ) and not any(marker in text for marker in ("了吗", "了没", "进度", "查询", "查看"))
    if any(marker in text for marker in ("上报事件", "报告事件", "安防事件上报")):
        slots.update(action="report_event", target="event")
    elif any(marker in text for marker in ("提交处置", "处置结果", "完成处置")):
        slots.update(action="submit_disposal", target="event")
    elif any(marker in text for marker in ("开始巡检", "开始任务", "执行巡检")):
        slots.update(action="start_task", target="task")
    elif any(marker in text for marker in ("追加记录", "添加记录", "补充记录")):
        slots.update(action="add_record", target="task")
    elif any(marker in text for marker in ("提交记录", "完成巡检记录", "结束巡检")):
        slots.update(action="submit_records", target="task")
    elif task_create:
        slots.update(action="create", target="task")
        create_target = re.search(r"(?:我要)?对(?P<target>.+?)进行巡检", text)
        if create_target:
            target = create_target.group("target").strip("，,。 ")
            point_match = re.search(
                r"\d+栋(?:\d+单元)?|地下车库|消防通道|楼栋大厅|小区出入口|公共设备间",
                target,
            )
            subject = re.sub(
                r"^\d+栋(?:\d+单元)?(?:所有)?|^(?:地下车库|消防通道|楼栋大厅|小区出入口|公共设备间)(?:所有)?",
                "",
                target,
            ).strip()
            slots["title"] = f"{subject or target}巡检"
            slots["description"] = f"对{target}进行巡检"
            slots["point"] = point_match.group(0) if point_match else target
    elif any(marker in text for marker in ("查询事件", "安防事件", "事件进度")):
        slots.update(action="query", target="event")
    elif task_query:
        slots.update(action="query", target="task")
    event_types = {
        "GAS_LEAK": ("燃气泄漏", "煤气泄漏", "燃气味"),
        "FIRE": ("火情", "着火", "失火"),
        "PERSONAL_SAFETY": ("人员安全", "有人被困", "人身危险", "人员受伤"),
        "EQUIPMENT_FAULT": ("设备故障", "设施故障", "设备隐患"),
    }
    for event_type, cues in event_types.items():
        if any(cue in text for cue in cues):
            slots["event_type"] = event_type
            break
    if slots.get("event_type") in {"GAS_LEAK", "FIRE", "PERSONAL_SAFETY"}:
        slots["risk_level"] = "HIGH_RISK"
    if slots.get("action") == "report_event" and not slots.get("description"):
        slots["description"] = text.strip() if len(text.strip()) > 4 else None
    if slots.get("action") == "report_event" and not slots.get("location"):
        location_match = re.search(
            r"\d+栋(?:\d+单元)?(?:厨房|客厅|卧室|楼道)?|"
            r"地下车库|消防通道|楼栋大厅|小区出入口|公共设备间|厨房|客厅|卧室|楼道",
            text,
        )
        if location_match:
            slots["location"] = location_match.group(0)
    return {key: value for key, value in slots.items() if value is not None}


def _is_contextual_reference(text: str) -> bool:
    return len((text or "").strip()) <= 24 and any(
        marker in (text or "")
        for marker in ("那", "刚才", "上个月", "上上个月", "本月", "这个月", "不是", "改成")
    )


class DeterministicModelGateway:
    """Production-safe keyword fallback used when no external model is available."""

    INTENT_KEYWORDS: dict[str, list[str]] = {
        "REPAIR": [
            "报修",
            "维修",
            "工单",
            "坏了",
            "漏水",
            "漏电",
            "故障",
            "破损",
            "堵塞",
        ],
        "ANNOUNCEMENT": ["公告", "通知", "通告", "发布", "告示", "停水", "停电", "供水", "供电"],
        "BILLING": ["账单", "缴费", "物业费", "费用", "收费", "欠费"],
        "INSPECTION": [
            "巡检",
            "安保",
            "巡逻",
            "安防",
            "隐患",
            "治安",
            "上报事件",
            "处置结果",
            "燃气泄漏",
            "煤气泄漏",
            "火情",
            "人员安全",
        ],
        "GENERAL_HELP": [
            "帮助",
            "帮忙",
            "你好",
            "您好",
            "能做什么",
            "怎么用",
            "使用说明",
            "服务范围",
            "社区服务",
            "守则",
            "物业电话",
            "联系方式",
            "联系电话",
            "停车",
            "装修",
            "门禁",
            "垃圾",
            "开放时间",
            "社区规定",
            "物业规定",
        ],
    }

    def __init__(self, *, today_provider=None) -> None:
        self._today_provider = today_provider or _business_today

    def ready(self) -> bool:
        return True

    def analyze(self, text: str) -> ModelAnalysis:
        intent, confidence = self.classify_intent(text)
        return ModelAnalysis(
            intent=intent,
            confidence=confidence,
            slots=self._slots_for_intent(text, intent),
            provider="keyword",
        )

    def analyze_with_context(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
    ) -> ModelAnalysis:
        today = trusted_business_date(
            trusted_context.get("business_date"), fallback=self._today_provider()
        )
        intent, confidence = self.classify_intent(text)
        result = ModelAnalysis(
            intent=intent,
            confidence=confidence,
            slots=(
                _deterministic_announcement_slots(text, today)
                if intent == Intent.ANNOUNCEMENT.value
                else self._slots_for_intent(text, intent)
            ),
            provider="keyword",
        )
        if result.intent != Intent.UNCERTAIN.value or not _is_contextual_reference(text):
            return result
        prior_intent = Intent.UNCERTAIN.value
        for message in reversed(history[-12:]):
            prior_intent, _ = self.classify_intent(str(message.get("content") or ""))
            if prior_intent != Intent.UNCERTAIN.value:
                break
        return ModelAnalysis(
            intent=prior_intent,
            confidence=0.65 if prior_intent != Intent.UNCERTAIN.value else result.confidence,
            slots=self._slots_for_intent(text, prior_intent),
            provider="keyword_context",
        )

    def _slots_for_intent(self, text: str, intent: str) -> dict[str, Any]:
        today = self._today_provider()
        if intent == Intent.REPAIR.value:
            return _deterministic_repair_slots(text)
        if intent == Intent.BILLING.value:
            return _deterministic_billing_slots(text, today)
        if intent == Intent.ANNOUNCEMENT.value:
            return _deterministic_announcement_slots(text, today)
        if intent == Intent.INSPECTION.value:
            return _deterministic_inspection_slots(text)
        return {}

    def classify_intent(self, text: str) -> tuple[str, float]:
        text = text or ""
        scores: dict[str, int] = {}
        for intent, keywords in self.INTENT_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    scores[intent] = scores.get(intent, 0) + 1
        if not scores:
            return Intent.UNCERTAIN.value, 0.3
        best = max(scores, key=scores.get)
        confidence = min(0.95, 0.6 + 0.1 * scores[best])
        return best, confidence

    def extract_slots(self, text: str, intent: str) -> dict[str, Any]:
        return {}

    def draft_announcement(self, *, topic: str, audience: Any, requirements: str) -> dict[str, str]:
        title = topic.strip()[:128] or "社区通知"
        scope = "相关住户"
        if isinstance(audience, dict) and audience.get("building_ids"):
            scope = "、".join(str(item) for item in audience["building_ids"]) + "住户"
        elif audience == {}:
            scope = "全体住户"
        detail = requirements.strip() or "具体安排请关注物业后续通知"
        return {
            "title": title,
            "body": (
                f"尊敬的{scope}：\n\n{detail}。请您提前做好相应安排，"
                "感谢理解与配合。\n\n物业服务中心"
            ),
            "category": "GENERAL",
        }

    def revise_announcement(
        self, *, draft: dict[str, str], audience: Any, instruction: str
    ) -> dict[str, str]:
        del draft, audience, instruction
        # A keyword fallback cannot safely rewrite prose. Report unavailability instead
        # of returning the unchanged draft as if the requested edit had succeeded.
        raise ModelGatewayError("公告智能修改暂不可用")
