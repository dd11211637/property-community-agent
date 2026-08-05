"""
Prompt 模板 - 物业社区管理AI智能体 · 费用查询与智能缴费模块
"""
from __future__ import annotations
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models_db import Bill

# ── LLM System Prompt ─────────────────────────────────

BILL_INTERPRET_SYSTEM_PROMPT = (
    "你是一名贴心的社区 AI 物业管家，名叫「小智」。"
    "请将给定的账单 JSON 数据转化为口语化、亲切且易懂的语言向业主解读。"
    "需详细说明每一笔费用构成（特别是公摊水电和滞纳金的原因），"
    "并提示业主如何快捷缴费。"
    "语气要温暖、耐心，像朋友在聊天一样。"
    "回复控制在 200 字以内，用第二人称「您」称呼业主。"
)


def build_interpret_user_prompt(bill: "Bill", user_name: str) -> str:
    """构建账单解读的 User Prompt"""
    status_map = {
        "UNPAID": "未到期（还不需要缴费，但请留意最迟缴费日）",
        "OVERDUE": "已逾期（已超过最迟缴费日，产生了滞纳金）",
        "PAID": "已缴费",
    }
    return f"""
请帮 {user_name} 业主解读以下账单：

- 账期：{bill.bill_period}
- 物业费：{float(bill.property_fee)} 元
- 公摊水电费：{float(bill.utility_fee)} 元
- 车位费：{float(bill.parking_fee)} 元
- 滞纳金：{float(bill.late_fee)} 元
- 合计：{float(bill.total_amount)} 元
- 最迟缴费日：{bill.due_date}
- 状态：{status_map.get(bill.status, bill.status)}

请用亲切的口语化语言解读。
"""


# ── 分层催缴提醒 ─────────────────────────────────────

def get_reminder(bill: "Bill", today: date | None = None) -> tuple[str, str]:
    """
    根据账单逾期天数返回提醒层级和文案
    - 未到期: gentle
    - 逾期 ≤ 30 天: short
    - 逾期 > 30 天: long
    """
    if today is None:
        today = date.today()

    if bill.status == "PAID":
        return "gentle", ""

    due = bill.due_date if isinstance(bill.due_date, date) else date.fromisoformat(str(bill.due_date))
    overdue_days = (today - due).days

    if overdue_days <= 0:
        return "gentle", (
            f"亲爱的业主，您的 {bill.bill_period} 账单已生成，"
            f"合计 {float(bill.total_amount):.2f} 元，"
            f"最迟缴费日为 {bill.due_date}，"
            "建议您尽早缴费，避免逾期产生滞纳金哦～"
        )
    elif overdue_days <= 30:
        return "short", (
            f"温馨提示：您的 {bill.bill_period} 账单已逾期 {overdue_days} 天，"
            f"当前欠费 {float(bill.total_amount):.2f} 元（含滞纳金 {float(bill.late_fee):.2f} 元），"
            "点击下方按钮即可一键缴费，方便快捷！"
        )
    else:
        return "long", (
            f"重要提醒：您的 {bill.bill_period} 账单已逾期 {overdue_days} 天，"
            f"累计欠费 {float(bill.total_amount):.2f} 元（含滞纳金 {float(bill.late_fee):.2f} 元）。"
            "长期欠费可能影响您的信用记录，建议立即缴费。"
            "如有困难，请联系您的专属管家协助处理。"
        )