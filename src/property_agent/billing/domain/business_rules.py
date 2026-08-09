"""
domain/business_rules.py     业务规则

核心业务逻辑，不依赖任何外部框架，纯函数。
SQL 等价标注在调用方（application/commands.py）。
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional
import csv
import io

from .enums import BillStatus, ReminderLevel
from .entities import Bill


# ── 滞纳金计算规则 ──────────────────────────────────

# 滞纳金费率：每日 0.1%（即千分之一）
LATE_FEE_RATE_PER_DAY = 0.001

# 滞纳金计算宽限期：到期日后 3 天内不计算
LATE_FEE_GRACE_DAYS = 3

# 滞纳金上限：不超过本金
LATE_FEE_MAX_RATIO = 1.0


def calculate_late_fee(bill: Bill, today: date | None = None) -> float:
    """
    计算账单的滞纳金。

    规则:
      1. 已缴费账单不计算
      2. 宽限期内（到期日 + 3 天）不计算
      3. 每日费率 = 0.1%
      4. 滞纳金上限 = 本金（物业费 + 公摊水电 + 车位费）
      5. 结果精确到分，向上取整

    SQL 等价:
        SELECT
            CASE
                WHEN status = 'PAID' THEN 0
                WHEN DATEDIFF(CURRENT_DATE, due_date) <= 3 THEN 0
                ELSE LEAST(
                    (property_fee + utility_fee + parking_fee) * 0.001 * (DATEDIFF(CURRENT_DATE, due_date) - 3),
                    (property_fee + utility_fee + parking_fee)
                )
            END AS late_fee
        FROM fee_bills WHERE bill_id = :bill_id;
    """
    if bill.status == BillStatus.PAID:
        return 0.0

    if today is None:
        today = date.today()

    due = date.fromisoformat(bill.due_date)
    overdue_days = (today - due).days

    if overdue_days <= LATE_FEE_GRACE_DAYS:
        return 0.0

    # 可计算天数 = 实际逾期天数 - 宽限期
    chargeable_days = overdue_days - LATE_FEE_GRACE_DAYS

    # 本金 = 物业费 + 公摊水电 + 车位费（不含已有滞纳金）
    principal = bill.property_fee + bill.utility_fee + bill.parking_fee

    late_fee = principal * LATE_FEE_RATE_PER_DAY * chargeable_days

    # 上限限制
    max_fee = principal * LATE_FEE_MAX_RATIO
    late_fee = min(late_fee, max_fee)

    # 精确到分，向上取整
    import math
    late_fee = math.ceil(late_fee * 100) / 100

    return round(late_fee, 2)


# ── 分层催缴提醒规则 ─────────────────────────────────

# 短期逾期阈值（天）
SHORT_OVERDUE_THRESHOLD = 30


def determine_reminder_level(bill: Bill, today: date | None = None) -> ReminderLevel:
    """
    根据逾期天数确定催缴层级。

    - 未到期 / 已缴费: GENTLE
    - 逾期 ≤ 30 天: SHORT_OVERDUE
    - 逾期 > 30 天: LONG_OVERDUE

    SQL 等价:
        SELECT
            CASE
                WHEN status = 'PAID' THEN 'gentle'
                WHEN DATEDIFF(CURRENT_DATE, due_date) <= 0 THEN 'gentle'
                WHEN DATEDIFF(CURRENT_DATE, due_date) <= 30 THEN 'short'
                ELSE 'long'
            END AS reminder_level
        FROM fee_bills WHERE bill_id = :bill_id;
    """
    if bill.status == BillStatus.PAID:
        return ReminderLevel.GENTLE

    overdue_days = bill.overdue_days(today)
    if overdue_days <= 0:
        return ReminderLevel.GENTLE
    elif overdue_days <= SHORT_OVERDUE_THRESHOLD:
        return ReminderLevel.SHORT_OVERDUE
    else:
        return ReminderLevel.LONG_OVERDUE


def generate_reminder_text(bill: Bill, reminder_level: ReminderLevel, today: date | None = None) -> str:
    """生成催缴提醒文案"""
    overdue_days = bill.overdue_days(today)

    templates = {
        ReminderLevel.GENTLE: (
            f"亲爱的业主，您的 {bill.bill_period} 账单已生成，"
            f"合计 {bill.total_amount:.2f} 元，"
            f"最迟缴费日为 {bill.due_date}，"
            "建议您尽早缴费，避免逾期产生滞纳金哦～"
        ),
        ReminderLevel.SHORT_OVERDUE: (
            f"温馨提示：您的 {bill.bill_period} 账单已逾期 {overdue_days} 天，"
            f"当前欠费 {bill.total_amount:.2f} 元（含滞纳金 {bill.late_fee:.2f} 元），"
            "点击下方按钮即可一键缴费，方便快捷！"
        ),
        ReminderLevel.LONG_OVERDUE: (
            f"重要提醒：您的 {bill.bill_period} 账单已逾期 {overdue_days} 天，"
            f"累计欠费 {bill.total_amount:.2f} 元（含滞纳金 {bill.late_fee:.2f} 元）。"
            "长期欠费可能影响您的信用记录，建议立即缴费。"
            "如有困难，请联系您的专属管家协助处理。"
        ),
    }
    return templates.get(reminder_level, "")


# ── 缴费校验规则 ─────────────────────────────────────

def validate_payable(bill: Bill) -> tuple[bool, str]:
    """
    校验账单是否可缴费。

    返回 (是否可缴费, 错误原因)

    SQL 等价:
        SELECT status FROM fee_bills WHERE bill_id = :bill_id;
        -- 应用层判断: status NOT IN ('PAID', 'CANCELLED')
    """
    if bill.status == BillStatus.PAID:
        return False, "该账单已缴费，无需重复支付"
    if bill.status == BillStatus.CANCELLED:
        return False, "该账单已作废，无法缴费"
    return True, ""


def validate_cancellable(bill: Bill) -> tuple[bool, str]:
    """
    校验账单是否可作废。

    规则:
      - 已缴费账单不可作废（需先退款）
      - 已作废账单不可重复作废
      - 仅管理员可操作（权限校验在适配器层）

    返回 (是否可作废, 错误原因)

    SQL 等价:
        SELECT status FROM fee_bills WHERE bill_id = :bill_id;
        -- 应用层判断: status NOT IN ('PAID', 'CANCELLED')
    """
    if bill.status == BillStatus.PAID:
        return False, "该账单已缴费，无法作废（如需作废请先退款）"
    if bill.status == BillStatus.CANCELLED:
        return False, "该账单已作废"
    return True, ""


# ── 退款校验规则 ─────────────────────────────────────

def validate_refundable(bill: Bill) -> tuple[bool, str]:
    """
    校验账单是否可退款。

    规则:
      - 仅已缴费账单可退款
      - 已退款的支付记录不可重复退款

    返回 (是否可退款, 错误原因)

    SQL 等价:
        SELECT f.status, p.pay_status
          FROM fee_bills f
          JOIN fee_payments p ON f.bill_id = p.bill_id
         WHERE f.bill_id = :bill_id;
        -- 应用层判断: f.status = 'PAID' AND p.pay_status != 'REFUNDED'
    """
    if bill.status != BillStatus.PAID:
        return False, "该账单尚未缴费，无法退款"
    return True, ""


# ── 账单自动生成规则 ─────────────────────────────────

# 公摊水电费系数（元/㎡·月）
DEFAULT_UTILITY_RATE = 0.50

# 账期生成：每月1号生成上月账单
# 到期日：每月15号


def generate_bill_id(room_id: str, period: str) -> str:
    """
    生成账单ID: bill_{YYYYMM}_{房号后缀}

    SQL 等价（PostgreSQL）:
        SELECT 'bill_' || REPLACE(:period, '-', '') || '_' || SPLIT_PART(:room_id, '_', 2);

    SQL 等价（SQLite）:
        SELECT 'bill_' || REPLACE(:period, '-', '') || '_' || SUBSTR(:room_id, INSTR(:room_id, '_') + 1);
    """
    period_str = period.replace("-", "")
    room_suffix = room_id.split("_")[-1]
    return f"bill_{period_str}_{room_suffix}"


def calculate_due_date(period: str) -> str:
    """
    计算到期日：账期月份 + 1 月的 15 号

    SQL 等价:
        SELECT DATE(SUBSTR(:period, 1, 4) || '-' ||
                    CAST(CAST(SUBSTR(:period, 6, 2) AS INTEGER) + 1 AS TEXT) ||
                    '-15') AS due_date;
    """
    year, month = int(period[:4]), int(period[5:])
    if month == 12:
        year += 1
        month = 1
    else:
        month += 1
    return f"{year}-{month:02d}-15"


def calculate_bill_fees(room: dict) -> dict:
    """
    根据房号信息计算账单费用。

    费用构成:
      - 物业费 = 建筑面积 × 物业费单价
      - 公摊水电费 = 建筑面积 × 公摊系数
      - 车位费 = 车位数 × 车位费单价

    SQL 等价:
        SELECT room_area * property_fee_rate AS property_fee,
               room_area * :utility_rate AS utility_fee,
               parking_spots * parking_fee_rate AS parking_fee
          FROM community_rooms
         WHERE room_id = :room_id;
    """
    import math
    property_fee = math.ceil(room["room_area"] * room["property_fee_rate"] * 100) / 100
    utility_fee = math.ceil(room["room_area"] * DEFAULT_UTILITY_RATE * 100) / 100
    parking_fee = math.ceil(room["parking_spots"] * room["parking_fee_rate"] * 100) / 100
    total = property_fee + utility_fee + parking_fee

    return {
        "property_fee": property_fee,
        "utility_fee": utility_fee,
        "parking_fee": parking_fee,
        "total_amount": round(total, 2),
    }


# ── 账单汇总规则 ─────────────────────────────────────

def summarize_bills(bills: list[Bill]) -> dict:
    """
    汇总账单数据。

    SQL 等价:
        SELECT
            COUNT(*) FILTER (WHERE status IN ('UNPAID','OVERDUE')) AS unpaid_count,
            COUNT(*) FILTER (WHERE status = 'PAID') AS paid_count,
            COUNT(*) FILTER (WHERE status = 'OVERDUE') AS overdue_count,
            COALESCE(SUM(total_amount) FILTER (WHERE status IN ('UNPAID','OVERDUE')), 0) AS total_unpaid
        FROM fee_bills WHERE user_id = :user_id;
    """
    unpaid = [b for b in bills if b.status in (BillStatus.UNPAID, BillStatus.OVERDUE)]
    paid = [b for b in bills if b.status == BillStatus.PAID]

    return {
        "total_unpaid": round(sum(b.total_amount for b in unpaid), 2),
        "unpaid_count": len(unpaid),
        "paid_count": len(paid),
        "overdue_count": len([b for b in unpaid if b.status == BillStatus.OVERDUE]),
    }


# ── 票据编号生成规则 ─────────────────────────────────

def generate_receipt_no(user_id: str, paid_at: datetime | None = None) -> str:
    """
    生成电子票据单号: REC_{YYYYMMDD}_{用户序号}

    SQL 等价（PostgreSQL）:
        SELECT 'REC_' || TO_CHAR(CURRENT_DATE, 'YYYYMMDD') || '_' || SPLIT_PART(:user_id, '_', 2);

    SQL 等价（SQLite）:
        SELECT 'REC_' || STRFTIME('%Y%m%d', 'now') || '_' || SUBSTR(:user_id, INSTR(:user_id, '_') + 1);
    """
    if paid_at is None:
        paid_at = datetime.now()
    today_str = paid_at.strftime("%Y%m%d")
    user_suffix = user_id.split("_")[-1]
    return f"REC_{today_str}_{user_suffix}"


def generate_payment_id(last_payment_id: str | None = None) -> str:
    """
    生成支付记录ID: pay_{序号}

    SQL 等价（PostgreSQL）:
        SELECT 'pay_' || LPAD(COALESCE(MAX(NULLIF(REGEXP_REPLACE(payment_id, '[^0-9]', '', 'g'), '')::INT), 0) + 1::TEXT, 3, '0')
        FROM fee_payments;

    SQL 等价（SQLite）:
        SELECT 'pay_' || SUBSTR('000' || CAST(COALESCE(MAX(CAST(REPLACE(payment_id, 'pay_', '') AS INTEGER)), 0) + 1 AS TEXT), -3, 3)
        FROM fee_payments;
    """
    if last_payment_id:
        seq = int(last_payment_id.split("_")[-1]) + 1
    else:
        seq = 1
    return f"pay_{seq:03d}"


def generate_transaction_id(payment_id: str, paid_at: datetime | None = None) -> str:
    """
    生成第三方支付流水号: TXN_{YYYYMMDD}_{序号}

    SQL 等价（PostgreSQL）:
        SELECT 'TXN_' || TO_CHAR(CURRENT_DATE, 'YYYYMMDD') || '_' || :seq_num;

    SQL 等价（SQLite）:
        SELECT 'TXN_' || STRFTIME('%Y%m%d', 'now') || '_' || :seq_num;
    """
    if paid_at is None:
        paid_at = datetime.now()
    today_str = paid_at.strftime("%Y%m%d")
    seq = payment_id.split("_")[-1]
    return f"TXN_{today_str}_{seq}"


# ── 账单导出规则 ─────────────────────────────────────

def bills_to_csv(bills: list[Bill]) -> str:
    """
    将账单列表导出为 CSV 格式字符串。

    表头: 账单ID, 业主, 楼栋, 房号, 账期, 物业费, 公摊水电费, 车位费, 滞纳金,
           合计, 最迟缴费日, 状态, 缴费时间, 票据编号

    SQL 等价:
        SELECT f.bill_id, u.user_name, b.building_name, r.room_number,
               f.bill_period, f.property_fee, f.utility_fee, f.parking_fee,
               f.late_fee, f.total_amount, f.due_date, f.status,
               f.payment_time, f.receipt_no
          FROM fee_bills f
          JOIN sys_users u ON f.user_id = u.user_id
          JOIN community_rooms r ON f.room_id = r.room_id
          JOIN community_buildings b ON r.building_id = b.building_id
         WHERE （根据角色过滤条件）
         ORDER BY f.bill_period DESC;
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # 表头
    writer.writerow([
        "账单ID", "业主", "楼栋", "房号", "账期",
        "物业费", "公摊水电费", "车位费", "滞纳金", "合计",
        "最迟缴费日", "状态", "缴费时间", "票据编号",
    ])

    status_map = {"UNPAID": "未到期", "OVERDUE": "已逾期", "PAID": "已缴费", "CANCELLED": "已作废"}

    for b in bills:
        writer.writerow([
            b.bill_id,
            b.user_name,
            b.building_name,
            b.room_number,
            b.bill_period,
            f"{b.property_fee:.2f}",
            f"{b.utility_fee:.2f}",
            f"{b.parking_fee:.2f}",
            f"{b.late_fee:.2f}",
            f"{b.total_amount:.2f}",
            b.due_date,
            status_map.get(b.status.value if hasattr(b.status, 'value') else b.status, b.status),
            b.payment_time or "",
            b.receipt_no or "",
        ])

    return output.getvalue()