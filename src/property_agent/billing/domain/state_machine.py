"""
domain/state_machine.py     账单状态机

定义账单状态之间的合法转换规则。
每个状态转换对应一条 SQL UPDATE 语句。
"""
from __future__ import annotations
from datetime import date, datetime
from .enums import BillStatus
from .entities import Bill


# 合法的状态转换表
# 每个转换对应一条 SQL UPDATE 语句
ALLOWED_TRANSITIONS: dict[BillStatus, set[BillStatus]] = {
    # UNPAID → OVERDUE: 系统自动（逾期检查）
    #   SQL: UPDATE fee_bills SET status='OVERDUE', updated_at=NOW() WHERE bill_id=:id;
    # UNPAID → PAID: 用户缴费
    #   SQL: UPDATE fee_bills SET status='PAID', payment_time=NOW(), receipt_no=:rn, updated_at=NOW() WHERE bill_id=:id;
    # UNPAID → CANCELLED: 管理员作废
    #   SQL: UPDATE fee_bills SET status='CANCELLED', updated_at=NOW() WHERE bill_id=:id;
    BillStatus.UNPAID:    {BillStatus.OVERDUE, BillStatus.PAID, BillStatus.CANCELLED},

    # OVERDUE → PAID: 逾期后补缴
    #   SQL: UPDATE fee_bills SET status='PAID', payment_time=NOW(), receipt_no=:rn, updated_at=NOW() WHERE bill_id=:id;
    # OVERDUE → CANCELLED: 管理员作废
    #   SQL: UPDATE fee_bills SET status='CANCELLED', updated_at=NOW() WHERE bill_id=:id;
    BillStatus.OVERDUE:   {BillStatus.PAID, BillStatus.CANCELLED},

    # PAID: 终态，不可再转换
    BillStatus.PAID:      set(),

    # CANCELLED: 终态，不可再转换
    BillStatus.CANCELLED: set(),
}


def can_transition(current: BillStatus, target: BillStatus) -> bool:
    """
    检查状态转换是否合法

    SQL:
        SELECT status FROM fee_bills WHERE bill_id = :bill_id;
        -- 应用层判断: 当前状态是否允许转换到目标状态
    """
    return target in ALLOWED_TRANSITIONS.get(current, set())


def transition_to(bill: Bill, target: BillStatus, **kwargs) -> Bill:
    """
    执行账单状态转换。

    SQL:
        -- UNPAID/OVERDUE → PAID:
        UPDATE fee_bills
           SET status = 'PAID',
               payment_time = NOW(),
               receipt_no = :receipt_no,
               updated_at = NOW()
         WHERE bill_id = :bill_id;

        -- UNPAID → OVERDUE:
        UPDATE fee_bills
           SET status = 'OVERDUE',
               updated_at = NOW()
         WHERE bill_id = :bill_id;

        -- UNPAID/OVERDUE → CANCELLED:
        UPDATE fee_bills
           SET status = 'CANCELLED',
               updated_at = NOW()
         WHERE bill_id = :bill_id;

    Raises:
        ValueError: 非法状态转换
    """
    if not can_transition(bill.status, target):
        raise ValueError(
            f"非法的状态转换: {bill.status.value} → {target.value}"
        )

    bill.status = target

    if target == BillStatus.PAID:
        bill.payment_time = kwargs.get("payment_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        bill.receipt_no = kwargs.get("receipt_no", "")

    return bill


def auto_check_overdue(bill: Bill, today: date | None = None) -> Bill:
    """
    自动检查并更新逾期状态。

    规则: 如果账单状态为 UNPAID 且当前日期已超过到期日，则自动转为 OVERDUE。
    此方法应在每次查询账单时调用，确保状态实时准确。

    SQL:
        -- 批量更新所有已逾期的 UNPAID 账单:
        UPDATE fee_bills
           SET status = 'OVERDUE',
               updated_at = NOW()
         WHERE status = 'UNPAID'
           AND due_date < CURRENT_DATE;

        -- 单笔检查:
        SELECT bill_id, status, due_date
          FROM fee_bills
         WHERE bill_id = :bill_id;
        -- 应用层判断: status = 'UNPAID' AND CURRENT_DATE > due_date
    """
    if bill.status != BillStatus.UNPAID:
        return bill

    if today is None:
        today = date.today()

    due = date.fromisoformat(bill.due_date)
    if today > due:
        bill.status = BillStatus.OVERDUE

    return bill