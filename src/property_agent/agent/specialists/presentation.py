"""User-facing summaries derived only from capability output facts."""

from __future__ import annotations

from typing import Any

_STATUS_LABELS = {
    "PENDING_ASSIGNMENT": "等待物业派单",
    "PENDING_ACCEPTANCE": "等待维修人员接单",
    "PROCESSING": "正在处理中",
    "PENDING_VERIFICATION": "等待您验收",
    "REWORKING": "正在返工",
    "CLOSED": "已完成",
    "OPEN": "待处理",
    "PENDING": "待处理",
    "COMPLETED": "已完成",
    "PLANNED": "待分派",
    "ASSIGNED": "已分派",
    "IN_PROGRESS": "巡检中",
    "SUBMITTED": "等待管理者复核",
    "REPORTED": "等待分派",
    "PENDING_REVIEW": "等待管理者复核",
    "UNPAID": "待缴费",
    "OVERDUE": "已逾期",
    "PAID": "已缴费",
    "CANCELLED": "已取消",
    "PUBLISHED": "已发布",
}


def present_success(capability: str, data: dict[str, Any]) -> str:
    """Render a concise response without inventing absent capability facts."""
    nested = data.get("data")
    if isinstance(nested, dict):
        data = nested
    if capability == "billing_query" and data.get("query_type") == "rule":
        return _present_billing_rule(data)
    for key, presenter in (
        ("work_order", _present_work_order),
        ("consultation", _present_consultation),
        ("task", _present_task),
        ("event", _present_event),
        ("announcement", _present_announcement),
        ("draft", _present_draft),
        ("bill", _present_bill),
    ):
        value = data.get(key)
        if isinstance(value, dict):
            return presenter(capability, value, data)
    if "count" in data:
        return _present_collection(capability, data)
    return "操作已完成。"


def _present_work_order(capability: str, item: dict[str, Any], data: dict[str, Any]) -> str:
    ident = _identifier(item)
    status = _status(item.get("status"))
    if capability == "repair_create":
        return f"报修已提交，工单号 {ident}，当前{status}。"
    timeline = data.get("timeline") or []
    latest = timeline[-1] if timeline else {}
    detail = latest.get("note") or latest.get("reason")
    return f"工单 {ident} 当前{status}。" + (f"最新进展：{detail}。" if detail else "")


def _present_consultation(_capability: str, item: dict[str, Any], _data: dict[str, Any]) -> str:
    return f"费用咨询已提交，咨询编号 {_identifier(item)}，物业工作人员会尽快处理。"


def _present_task(capability: str, item: dict[str, Any], _data: dict[str, Any]) -> str:
    ident = _identifier(item)
    status = _status(item.get("status"))
    labels = {
        "inspection_create": f"巡检任务已创建，任务编号 {ident}，当前{status}。",
        "inspection_start_task": f"已开始巡检，任务 {ident} 当前{status}。",
        "inspection_add_record": f"巡检记录已追加，任务 {ident} 仍处于{status}。",
        "inspection_submit_record": f"最终巡检记录已提交，任务 {ident} 当前{status}。",
        "inspection_submit_records": f"最终巡检记录已提交，任务 {ident} 当前{status}。",
    }
    return labels.get(capability, f"巡检信息已更新，任务 {ident} 当前{status}。")


def _present_event(capability: str, item: dict[str, Any], _data: dict[str, Any]) -> str:
    ident = _identifier(item)
    status = _status(item.get("status"))
    if capability == "security_event_create":
        prefix = "高风险安防事件" if item.get("risk_level") == "HIGH_RISK" else "安防事件"
        return f"{prefix}已上报，事件编号 {ident}，当前{status}，已转人工处置。"
    if capability == "security_event_submit_disposal":
        return f"事件 {ident} 的处置记录已提交，当前{status}。"
    return f"事件 {ident} 当前{status}。"


def _present_announcement(capability: str, item: dict[str, Any], _data: dict[str, Any]) -> str:
    title = str(item.get("title") or "该公告")
    if capability == "announcement_create_draft":
        return f"AI 稿件已保存为“{title}”草稿，请在公告页面审稿并送审。"
    if capability == "announce_publish":
        return f"“{title}”已发布，消息正在发送给公告受众。"
    if capability == "announcement_schedule_publish":
        return f"“{title}”已预约在 {item.get('scheduled_at')} 发布。"
    return f"已找到“{title}”，当前{_status(item.get('status'))}。"


def _present_draft(capability: str, item: dict[str, Any], _data: dict[str, Any]) -> str:
    lead = (
        "我已按你的要求修改公告："
        if capability == "announcement_revise"
        else "我已根据主题和受众起草公告："
    )
    return (
        f"{lead}\n\n{item.get('title')}\n\n{item.get('body')}\n\n"
        "请审阅；可以继续告诉我修改要求，或回复“采用这个稿件并保存草稿”。"
    )


def _present_bill(_capability: str, item: dict[str, Any], _data: dict[str, Any]) -> str:
    amount = item.get("total_amount") or item.get("amount")
    period = item.get("period")
    parts = [f"{period}账单" if period else "账单"]
    if amount not in (None, ""):
        parts.append(f"金额 {amount} 元")
    parts.append(f"当前{_status(item.get('status'))}")
    return "，".join(parts) + "。"


def _present_collection(capability: str, data: dict[str, Any]) -> str:
    count = int(data.get("count") or 0)
    items = data.get("items") or []
    if capability == "billing_query" and count == 1 and isinstance(items[0], dict):
        return _present_single_bill(items[0])
    if capability == "announcement_list":
        return _present_announcement_list(count, items, data)
    if capability == "community_knowledge_search":
        return _present_knowledge(count, items)
    if capability == "inspection_list" and data.get("target") == "task":
        return _present_inspection_tasks(items, data)
    if capability == "inspection_list" and data.get("target") == "event":
        return (
            "当前没有符合查询范围的安防事件。" if count == 0 else f"已为您找到 {count} 项安防事件。"
        )
    labels = {
        "repair_list": "报修记录",
        "billing_query": "账单",
        "inspection_list": "巡检记录",
        "announcement_list": "公告",
    }
    label = labels.get(capability, "相关记录")
    return f"暂时没有{label}。" if count == 0 else f"已为您找到 {count} 条{label}。"


def _present_single_bill(item: dict[str, Any]) -> str:
    fee_labels = (
        ("物业费", "property_fee"),
        ("水电费", "utility_fee"),
        ("停车费", "parking_fee"),
        ("滞纳金", "late_fee"),
    )
    details = [
        f"{label} {item.get(key)} 元"
        for label, key in fee_labels
        if str(item.get(key) or "0") not in {"0", "0.0", "0.00"}
    ]
    breakdown = f"，其中{'、'.join(details)}" if details else ""
    amount = item.get("total_amount") or item.get("amount")
    return (
        f"我查到 {item.get('period') or '该期'} 账单共 {amount} 元{breakdown}，"
        f"目前状态为{_status(item.get('status'))}。"
    )


def _present_billing_rule(data: dict[str, Any]) -> str:
    rule = data.get("rule")
    if not isinstance(rule, dict):
        return "当前没有找到对应的有效收费规则。"
    validity = ""
    if rule.get("valid_from") or rule.get("valid_until"):
        validity = (
            f"，有效期 {rule.get('valid_from') or '未注明'} 至 {rule.get('valid_until') or '长期'}"
        )
    return (
        f"收费规则“{rule.get('name')}”（版本 {rule.get('version')}）"
        f"，参数为 {rule.get('parameters') or {}}{validity}。"
    )


def _present_announcement_list(count: int, items: list[Any], data: dict[str, Any]) -> str:
    topic = {"WATER_OUTAGE": "停水", "POWER_OUTAGE": "停电"}.get(
        str(data.get("topic") or ""), "相关"
    )
    scope = data.get("query_scope") or {}
    location = str(scope.get("community_name") or "当前社区")
    if scope.get("building"):
        building = str(scope["building"])
        location += f" {building if building.endswith('栋') else building + '栋'}"
    prefix = f"我查询了 {data.get('target_date') or '当前'} 适用于{location}的已发布{topic}公告，"
    if count == 0:
        return (
            prefix + f"目前没有找到匹配通知。这个结果只代表现有公告记录；"
            f"如果已经发生{topic}，建议联系物业确认临时故障。"
        )
    titles = "、".join(f"“{item.get('title')}”" for item in items[:3])
    return prefix + f"找到 {count} 条：{titles}。"


def _present_knowledge(count: int, items: list[Any]) -> str:
    if count == 0:
        return (
            "我检索了当前社区对住户可见的已发布正式资料，暂时没有找到"
            "与这个问题匹配的内容。为避免提供过期资料，建议联系物业工作人员确认。"
        )
    sources = "、".join(f"“{item.get('source_name') or item.get('title')}”" for item in items[:3])
    return f"我在当前社区已发布的正式资料中找到 {count} 条相关内容：{sources}。"


def _present_inspection_tasks(items: list[Any], data: dict[str, Any]) -> str:
    total = int(data.get("total") or 0)
    completed = int(data.get("completed") or 0)
    incomplete = int(data.get("incomplete") or 0)
    if total == 0:
        return "当前没有符合查询范围的巡检任务。"
    if incomplete == 0:
        return f"当前查询范围内共有 {total} 项巡检任务，已全部完成。"
    unfinished = [item for item in items if item.get("status") != "COMPLETED"]
    titles = "、".join(
        f"“{item.get('title')}”（{_status(item.get('status'))}）" for item in unfinished[:3]
    )
    detail = f"未完成的包括：{titles}。" if titles else ""
    return (
        f"当前共有 {total} 项巡检任务，已完成 {completed} 项，还有 {incomplete} 项未完成。{detail}"
    )


def _status(value: Any) -> str:
    text = str(value or "").strip()
    return _STATUS_LABELS.get(text, "处理中" if text else "")


def _identifier(item: dict[str, Any]) -> str:
    return str(item.get("business_no") or item.get("bill_id") or item.get("id") or "").strip()
