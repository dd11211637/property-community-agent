"""结果解释节点 — PRD §6.5.2（事实与建议分离）。

只根据工具返回的**事实**作答：
* 工具抛错 —— 如实展示错误；
* 工具返回接管指令 —— 说明已转人工，并同步 ``handover_required``；
* 工具返回业务失败（如账单源不可用）—— 展示真实错误码，不编造数据；
* 成功 —— 给出可核对的要点摘要。
"""

from typing import Any

from property_agent.agent.state import GraphState

_STATUS_LABELS = {
    "PENDING_ASSIGNMENT": "等待物业派单",
    "PENDING_ACCEPTANCE": "等待维修人员接单",
    "PROCESSING": "正在处理中",
    "PENDING_VERIFICATION": "等待您验收",
    "REWORKING": "正在返工",
    "CLOSED": "已完成",
    "DRAFT": "草稿",
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


def _status(value: Any) -> str:
    text = str(value or "").strip()
    return _STATUS_LABELS.get(text, "处理中" if text else "")


def _identifier(obj: dict[str, Any]) -> str:
    return str(obj.get("business_no") or obj.get("bill_id") or obj.get("id") or "").strip()


def _success_message(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    tool = result.get("tool")

    work_order = data.get("work_order")
    if isinstance(work_order, dict):
        ident = _identifier(work_order)
        status = _status(work_order.get("status"))
        if tool == "repair_create":
            return f"报修已提交，工单号 {ident}，当前{status}。"
        timeline = data.get("timeline") or []
        latest = timeline[-1] if timeline else {}
        detail = latest.get("note") or latest.get("reason")
        return f"工单 {ident} 当前{status}。" + (f"最新进展：{detail}。" if detail else "")

    consultation = data.get("consultation")
    if isinstance(consultation, dict):
        ident = _identifier(consultation)
        return f"费用咨询已提交，咨询编号 {ident}，物业工作人员会尽快处理。"

    task = data.get("task")
    if isinstance(task, dict):
        ident = _identifier(task)
        status = _status(task.get("status"))
        if tool == "inspection_create":
            return f"巡检任务已创建，任务编号 {ident}，当前{status}。"
        if tool == "inspection_start_task":
            return f"已开始巡检，任务 {ident} 当前{status}。"
        if tool == "inspection_add_record":
            return f"巡检记录已追加，任务 {ident} 仍处于{status}。"
        if tool in {"inspection_submit_record", "inspection_submit_records"}:
            return f"最终巡检记录已提交，任务 {ident} 当前{status}。"
        return f"巡检信息已更新，任务 {ident} 当前{status}。"

    event = data.get("event")
    if isinstance(event, dict):
        ident = _identifier(event)
        status = _status(event.get("status"))
        if tool == "security_event_create":
            prefix = "高风险安防事件" if event.get("risk_level") == "HIGH_RISK" else "安防事件"
            return f"{prefix}已上报，事件编号 {ident}，当前{status}。"
        if tool == "security_event_submit_disposal":
            return f"事件 {ident} 的处置记录已提交，当前{status}。"
        return f"事件 {ident} 当前{status}。"

    announcement = data.get("announcement")
    if isinstance(announcement, dict):
        title = str(announcement.get("title") or "该公告")
        status = _status(announcement.get("status"))
        if tool == "announcement_create_draft":
            return f"AI 稿件已保存为“{title}”草稿，请在公告页面审稿并送审。"
        if tool == "announce_publish":
            return f"“{title}”已发布，消息正在发送给公告受众。"
        if tool == "announcement_schedule_publish":
            return f"“{title}”已预约在 {announcement.get('scheduled_at')} 发布。"
        return f"已找到“{title}”，当前{status}。"

    draft = data.get("draft")
    if isinstance(draft, dict):
        lead = (
            "我已按你的要求修改公告："
            if tool == "announcement_revise"
            else "我已根据主题和受众起草公告："
        )
        return (
            f"{lead}\n\n{draft.get('title')}\n\n"
            f"{draft.get('body')}\n\n请审阅；可以继续告诉我修改要求，"
            "或回复“采用这个稿件并保存草稿”。"
        )

    bill = data.get("bill")
    if isinstance(bill, dict):
        amount = bill.get("total_amount") or bill.get("amount")
        period = bill.get("period")
        parts = [f"{period}账单" if period else "账单"]
        if amount not in (None, ""):
            parts.append(f"金额 {amount} 元")
        parts.append(f"当前{_status(bill.get('status'))}")
        return "，".join(parts) + "。"

    if "count" in data:
        count = int(data.get("count") or 0)
        items = data.get("items") or []
        if tool == "billing_query" and count == 1 and isinstance(items[0], dict):
            item = items[0]
            period = str(item.get("period") or "该期")
            amount = item.get("total_amount") or item.get("amount")
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
            return (
                f"我查到 {period} 账单共 {amount} 元{breakdown}，"
                f"目前状态为{_status(item.get('status'))}。"
            )
        if tool == "announcement_list":
            topic_label = {
                "WATER_OUTAGE": "停水",
                "POWER_OUTAGE": "停电",
            }.get(str(data.get("topic") or ""), "相关")
            target_date = str(data.get("target_date") or "").strip()
            scope = data.get("query_scope") or {}
            location = str(scope.get("community_name") or "当前社区")
            if scope.get("building"):
                building = str(scope["building"])
                location += f" {building if building.endswith('栋') else building + '栋'}"
            date_label = target_date or "当前"
            if count == 0:
                return (
                    f"我查询了 {date_label} 适用于{location}的已发布{topic_label}公告，"
                    f"目前没有找到匹配通知。这个结果只代表现有公告记录；"
                    f"如果已经发生{topic_label}，建议联系物业确认临时故障。"
                )
            titles = "、".join(f"“{item.get('title')}”" for item in items[:3])
            return (
                f"我查询了 {date_label} 适用于{location}的已发布{topic_label}公告，"
                f"找到 {count} 条：{titles}。"
            )
        if tool == "community_knowledge":
            if count == 0:
                return (
                    "我检索了当前社区对住户可见的已发布正式资料，暂时没有找到"
                    "与这个问题匹配的内容。为避免提供过期资料，建议联系物业工作人员确认。"
                )
            sources = "、".join(
                f"“{item.get('source_name') or item.get('title')}”" for item in items[:3]
            )
            return f"我在当前社区已发布的正式资料中找到 {count} 条相关内容：{sources}。"
        if tool == "inspection_list" and data.get("target") == "task":
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
                f"当前共有 {total} 项巡检任务，已完成 {completed} 项，"
                f"还有 {incomplete} 项未完成。{detail}"
            )
        if tool == "inspection_list" and data.get("target") == "event":
            return (
                "当前没有符合查询范围的安防事件。"
                if count == 0
                else f"已为您找到 {count} 项安防事件。"
            )
        labels = {
            "repair_list": "报修记录",
            "billing_query": "账单",
            "inspection_list": "巡检记录",
            "announcement_list": "公告",
        }
        label = labels.get(str(tool), "相关记录")
        period = str(data.get("period") or "").strip()
        if tool == "billing_query" and period:
            try:
                year, month = period.split("-", 1)
                label = f"{int(year)} 年 {int(month)} 月账单"
            except (TypeError, ValueError):
                label = f"{period} 账单"
        return f"暂时没有{label}。" if count == 0 else f"已为您找到 {count} 条{label}。"

    return "操作已完成。"


def _error_message(error: Any) -> str:
    text = str(error or "")
    if "Parameters have changed" in text:
        return "提交的信息已经发生变化，请重新核对并确认。"
    if "发布公告的权限" in text or "没有发布公告" in text:
        return text  # 越权拒绝等明确的权限提示原样返回
    if (
        "not configured" in text.lower()
        or "unavailable" in text.lower()
        or text == "PLANNER_UNAVAILABLE"
    ):
        return "服务暂时不可用，请稍后再试或联系物业工作人员。"
    if "没有找到该工单" in text or "工单号格式不正确" in text:
        return text
    return "暂时未能完成，请稍后重试；如仍有问题，请联系物业工作人员。"


def explain_result_node():
    def node(state: GraphState) -> GraphState:
        if state.error:
            state.add_message("assistant", _error_message(state.error))
            return state

        result = state.tool_result or {}
        if result.get("handover_required") or state.handover_required:
            state.handover_required = True
            data = result.get("data") or {}
            event = data.get("event")
            if result.get("ok") is True and isinstance(event, dict):
                ident = _identifier(event)
                state.add_message(
                    "assistant",
                    f"高风险安防事件已上报，事件编号 {ident}，系统已通知值班人员并转入人工处置。",
                )
                return state
            reason = result.get("reason") or "该操作为高风险，需授权人工处理。"
            state.add_message("assistant", f"已转人工处理：{reason}")
            return state

        if result.get("ok") is False:
            reason = result.get("reason") or result.get("error_code") or "未知错误"
            state.add_message("assistant", _error_message(reason))
            return state

        # Tool facts remain available in the structured ``facts`` response;
        # chat copy deliberately avoids internal intent/tool/status enums.
        state.add_message("assistant", _success_message(result))
        return state

    return node
