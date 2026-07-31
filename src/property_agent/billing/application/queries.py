"""
application/queries.py     查询（Query）

只读操作，不修改状态。
每个查询方法标注了对应的等价 SQL 语句。
"""
from __future__ import annotations
from typing import Optional

from .ports import BillRepository, UserRepository, ReceiptRepository, PaymentRepository
from ..domain.entities import Bill, User, Receipt, Payment
from ..domain.enums import BillStatus, UserRole
from ..domain.state_machine import auto_check_overdue
from ..domain.business_rules import summarize_bills, calculate_late_fee
from .dtos import (
    BillSummaryDTO, bill_to_dto, user_to_dto, ReceiptDTO,
    PaymentHistoryDTO, PaymentHistoryResponseDTO, payment_to_dto,
)


class GetBillsByUser:
    """
    按用户查询账单（含滞纳金自动重算）

    SQL:
        SELECT * FROM fee_bills
        WHERE user_id = :user_id
        ORDER BY bill_period DESC;
    """

    def __init__(self, bill_repo: BillRepository):
        self._bill_repo = bill_repo

    def execute(self, user_id: str) -> BillSummaryDTO:
        bills = self._bill_repo.find_by_user(user_id)
        # 自动检查逾期状态 + 重算滞纳金
        for b in bills:
            auto_check_overdue(b)
            if b.status in (BillStatus.UNPAID, BillStatus.OVERDUE):
                new_late = calculate_late_fee(b)
                b.late_fee = new_late
                b.total_amount = b.property_fee + b.utility_fee + b.parking_fee + new_late
        summary = summarize_bills(bills)
        return BillSummaryDTO(
            total_unpaid=summary["total_unpaid"],
            unpaid_count=summary["unpaid_count"],
            overdue_count=summary["overdue_count"],
            paid_count=summary["paid_count"],
            bills=[bill_to_dto(b) for b in bills],
        )


class GetBillsByRole:
    """
    按角色查询账单（含滞纳金自动重算）

    SQL (owner):
        SELECT * FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;

    SQL (staff):
        SELECT f.* FROM fee_bills f
        JOIN community_rooms r ON f.room_id = r.room_id
        WHERE r.building_id = :building_id
        ORDER BY f.bill_period DESC;

    SQL (admin):
        SELECT * FROM fee_bills ORDER BY bill_period DESC;
    """

    def __init__(self, bill_repo: BillRepository, user_repo: UserRepository):
        self._bill_repo = bill_repo
        self._user_repo = user_repo

    def execute(self, user_id: str, role: str) -> BillSummaryDTO:
        if role == "owner":
            bills = self._bill_repo.find_by_user(user_id)
        elif role == "staff":
            staff = self._user_repo.find_by_id(user_id)
            if staff and staff.building_id:
                bills = self._bill_repo.find_by_building(staff.building_id)
            else:
                bills = []
        elif role == "admin":
            bills = self._bill_repo.find_all()
        else:
            bills = []

        # 自动检查逾期状态 + 重算滞纳金
        for b in bills:
            auto_check_overdue(b)
            if b.status in (BillStatus.UNPAID, BillStatus.OVERDUE):
                new_late = calculate_late_fee(b)
                b.late_fee = new_late
                b.total_amount = b.property_fee + b.utility_fee + b.parking_fee + new_late

        summary = summarize_bills(bills)
        return BillSummaryDTO(
            total_unpaid=summary["total_unpaid"],
            unpaid_count=summary["unpaid_count"],
            overdue_count=summary["overdue_count"],
            paid_count=summary["paid_count"],
            bills=[bill_to_dto(b) for b in bills],
        )


class GetBillById:
    """
    按 ID 查询单笔账单（含滞纳金自动重算）

    SQL:
        SELECT * FROM fee_bills WHERE bill_id = :bill_id;
    """

    def __init__(self, bill_repo: BillRepository):
        self._bill_repo = bill_repo

    def execute(self, bill_id: str) -> Optional[Bill]:
        bill = self._bill_repo.find_by_id(bill_id)
        if bill:
            auto_check_overdue(bill)
            if bill.status in (BillStatus.UNPAID, BillStatus.OVERDUE):
                new_late = calculate_late_fee(bill)
                bill.late_fee = new_late
                bill.total_amount = bill.property_fee + bill.utility_fee + bill.parking_fee + new_late
        return bill


class GetUserById:
    """
    按 ID 查询用户

    SQL:
        SELECT * FROM sys_users WHERE user_id = :user_id;
    """

    def __init__(self, user_repo: UserRepository):
        self._user_repo = user_repo

    def execute(self, user_id: str) -> Optional[dict]:
        user = self._user_repo.find_by_id(user_id)
        if not user:
            return None
        return user_to_dto(user)


class GetReceiptByNo:
    """
    按票据号查询票据

    SQL:
        SELECT * FROM fee_receipts WHERE receipt_no = :receipt_no;
    """

    def __init__(self, receipt_repo: ReceiptRepository):
        self._receipt_repo = receipt_repo

    def execute(self, receipt_no: str) -> Optional[ReceiptDTO]:
        receipt = self._receipt_repo.find_by_no(receipt_no)
        if not receipt:
            return None
        return ReceiptDTO(
            receipt_no=receipt.receipt_no,
            bill_id=receipt.bill_id,
            user_id=receipt.user_id,
            user_name=receipt.user_name,
            building=receipt.building_name,
            room=receipt.room_number,
            period=receipt.period,
            items={
                "property_fee": receipt.property_fee,
                "utility_fee": receipt.utility_fee,
                "parking_fee": receipt.parking_fee,
                "late_fee": receipt.late_fee,
            },
            total_amount=receipt.total_amount,
            payment_time=receipt.payment_time,
            issue_time=receipt.issue_time,
        )


# ── 新增: 支付历史查询 ────────────────────────────────

class GetPaymentHistoryByUser:
    """
    按用户查询支付历史

    SQL:
        SELECT p.*, u.user_name
          FROM fee_payments p
          JOIN sys_users u ON p.user_id = u.user_id
         WHERE p.user_id = :user_id
         ORDER BY p.paid_at DESC;
    """

    def __init__(self, payment_repo: PaymentRepository):
        self._payment_repo = payment_repo

    def execute(self, user_id: str) -> PaymentHistoryResponseDTO:
        payments = self._payment_repo.find_by_user(user_id)
        total_amount = sum(p.pay_amount for p in payments)
        return PaymentHistoryResponseDTO(
            payments=[payment_to_dto(p) for p in payments],
            total_count=len(payments),
            total_amount=round(total_amount, 2),
        )


class GetPaymentHistoryAll:
    """
    查询所有支付历史（管理员用）

    SQL:
        SELECT * FROM fee_payments ORDER BY paid_at DESC;
    """

    def __init__(self, payment_repo: PaymentRepository):
        self._payment_repo = payment_repo

    def execute(self) -> PaymentHistoryResponseDTO:
        payments = self._payment_repo.find_all()
        total_amount = sum(p.pay_amount for p in payments)
        return PaymentHistoryResponseDTO(
            payments=[payment_to_dto(p) for p in payments],
            total_count=len(payments),
            total_amount=round(total_amount, 2),
        )