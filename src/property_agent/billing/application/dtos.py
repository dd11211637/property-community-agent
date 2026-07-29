"""
application/dtos.py     数据传输对象（DTO）

用于应用层与适配器层之间传递数据，不含业务逻辑。
每个 DTO 对应 API 请求/响应的数据结构。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BillDTO:
    """
    账单数据传输对象

    对应 SQL 查询结果:
        SELECT f.bill_id, f.user_id, f.bill_period AS period,
               f.property_fee, f.utility_fee, f.parking_fee, f.late_fee,
               f.total_amount, f.due_date, f.status,
               f.payment_time, f.receipt_no,
               u.user_name, b.building_name, r.room_number
          FROM fee_bills f
          JOIN sys_users u ON f.user_id = u.user_id
          JOIN community_rooms r ON f.room_id = r.room_id
          JOIN community_buildings b ON r.building_id = b.building_id;
    """
    bill_id: str
    user_id: str
    period: str
    property_fee: float
    utility_fee: float
    parking_fee: float
    late_fee: float
    total_amount: float
    due_date: str
    status: str
    payment_time: Optional[str] = None
    receipt_no: Optional[str] = None
    user_name: str = ""
    building_name: str = ""
    room_number: str = ""


@dataclass
class BillSummaryDTO:
    """
    账单汇总 DTO

    对应 SQL:
        SELECT
            COALESCE(SUM(CASE WHEN status IN ('UNPAID','OVERDUE') THEN total_amount END), 0) AS total_unpaid,
            COUNT(CASE WHEN status IN ('UNPAID','OVERDUE') THEN 1 END) AS unpaid_count,
            COUNT(CASE WHEN status = 'PAID' THEN 1 END) AS paid_count,
            COUNT(CASE WHEN status = 'OVERDUE' THEN 1 END) AS overdue_count
          FROM fee_bills
         WHERE user_id = :user_id;
    """
    total_unpaid: float = 0.0
    unpaid_count: int = 0
    paid_count: int = 0
    overdue_count: int = 0
    bills: list[BillDTO] = field(default_factory=list)


@dataclass
class InterpretRequestDTO:
    """账单解读请求 DTO"""
    bill_id: str
    user_id: str = "user_101"


@dataclass
class InterpretResponseDTO:
    """
    账单解读响应 DTO

    对应 API: POST /api/bills/interpret
    包含 LLM 解读文本 + 催缴层级 + 催缴文案
    """
    bill_id: str
    interpretation: str
    reminder_level: str
    reminder_text: str


@dataclass
class PayRequestDTO:
    """缴费请求 DTO"""
    bill_id: str
    user_id: str = "user_101"


@dataclass
class PayResponseDTO:
    """
    缴费响应 DTO

    对应 SQL 事务:
        BEGIN;
        UPDATE fee_bills SET status='PAID', payment_time=NOW(), receipt_no=:rn WHERE bill_id=:id;
        INSERT INTO fee_payments (...) VALUES (...);
        INSERT INTO fee_receipts (...) VALUES (...);
        COMMIT;
    """
    success: bool
    message: str
    bill_id: str
    receipt_no: str
    paid_amount: float
    payment_time: str


@dataclass
class ReceiptDTO:
    """
    电子票据 DTO

    对应 SQL:
        SELECT r.*, u.user_name, b.building_name, rm.room_number,
               p.paid_at AS payment_time
          FROM fee_receipts r
          JOIN sys_users u ON r.user_id = u.user_id
          JOIN fee_bills f ON r.bill_id = f.bill_id
          JOIN community_rooms rm ON f.room_id = rm.room_id
          JOIN community_buildings b ON rm.building_id = b.building_id
          JOIN fee_payments p ON r.payment_id = p.payment_id
         WHERE r.receipt_no = :receipt_no;
    """
    receipt_no: str
    bill_id: str
    user_id: str
    user_name: str
    building: str
    room: str
    period: str
    items: dict  # {property_fee, utility_fee, parking_fee, late_fee}
    total_amount: float
    payment_time: str
    issue_time: str
    note: str = "电子票据，与纸质票据具有同等效力"


@dataclass
class UserDTO:
    """
    用户信息 DTO

    对应 SQL:
        SELECT u.user_id, u.user_name AS name, u.role, u.phone,
               b.building_name AS building, r.room_number AS room
          FROM sys_users u
          LEFT JOIN community_buildings b ON u.building_id = b.building_id
          LEFT JOIN community_rooms r ON u.room_id = r.room_id
         WHERE u.user_id = :user_id;
    """
    user_id: str
    name: str
    role: str
    building: str
    room: str
    phone: str


@dataclass
class BatchPayRequestDTO:
    """批量缴费请求 DTO"""
    bill_ids: list[str]
    user_id: str = "user_101"


@dataclass
class BatchPayResponseDTO:
    """
    批量缴费响应 DTO

    对应 SQL: 对每笔账单循环执行单笔缴费事务
    """
    success_count: int
    failed_count: int
    results: list[PayResponseDTO] = field(default_factory=list)
    total_paid: float = 0.0


# ── 新增: 支付历史 DTO ──────────────────────────────

@dataclass
class PaymentHistoryDTO:
    """
    支付记录 DTO

    对应 SQL:
        SELECT p.payment_id, p.bill_id, p.user_id, u.user_name,
               p.pay_amount, p.pay_method, p.pay_status,
               p.transaction_id, p.receipt_no, p.paid_at
          FROM fee_payments p
          JOIN sys_users u ON p.user_id = u.user_id
         ORDER BY p.paid_at DESC;
    """
    payment_id: str
    bill_id: str
    user_id: str
    user_name: str
    pay_amount: float
    pay_method: str
    pay_status: str
    transaction_id: str
    receipt_no: str
    paid_at: str


@dataclass
class PaymentHistoryResponseDTO:
    """
    支付历史响应 DTO

    对应 API: GET /api/bills/payments/history
    """
    payments: list[PaymentHistoryDTO] = field(default_factory=list)
    total_count: int = 0
    total_amount: float = 0.0


# ── 新增: 账单导出 DTO ──────────────────────────────

@dataclass
class ExportResponseDTO:
    """
    账单导出响应 DTO

    对应 API: GET /api/bills/export
    返回 CSV 格式字符串，包含完整的账单关联信息
    """
    csv_content: str
    filename: str
    total_count: int


# ── 新增: 取消账单 DTO ───────────────────────────────

@dataclass
class CancelRequestDTO:
    """取消账单请求 DTO"""
    bill_id: str
    reason: str = "管理员手动作废"


@dataclass
class CancelResponseDTO:
    """
    取消账单响应 DTO

    对应 SQL:
        UPDATE fee_bills
           SET status = 'CANCELLED',
               updated_at = NOW()
         WHERE bill_id = :bill_id;
    """
    success: bool
    message: str
    bill_id: str
    previous_status: str


# ── 新增: 退款 DTO ───────────────────────────────────

@dataclass
class RefundRequestDTO:
    """退款请求 DTO"""
    bill_id: str
    reason: str = "管理员退款"


@dataclass
class RefundResponseDTO:
    """
    退款响应 DTO

    对应 SQL:
        BEGIN;
        UPDATE fee_bills SET status='UNPAID', payment_time=NULL, receipt_no=NULL WHERE bill_id=:id;
        UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:id;
        UPDATE fee_receipts SET is_valid=FALSE WHERE bill_id=:id;
        COMMIT;
    """
    success: bool
    message: str
    bill_id: str
    refund_time: str


# ── 新增: 账单生成 DTO ───────────────────────────────

@dataclass
class GenerateBillsRequestDTO:
    """账单生成请求 DTO"""
    period: str = ""  # YYYY-MM，为空则自动取上月


@dataclass
class GenerateBillsResponseDTO:
    """
    账单生成响应 DTO

    对应 SQL:
        INSERT INTO fee_bills (...) VALUES (...)  -- 每间房一条
    """
    generated_count: int
    skipped_count: int
    period: str
    due_date: str


# ── 新增: 逾期检查 DTO ───────────────────────────────

@dataclass
class CheckOverdueResponseDTO:
    """
    逾期检查响应 DTO

    对应 SQL:
        UPDATE fee_bills SET status='OVERDUE' WHERE status='UNPAID' AND due_date < CURRENT_DATE;
    """
    updated_count: int
    total_checked: int
    check_time: str


# ── 新增: 分页 DTO ───────────────────────────────────

@dataclass
class PaginatedBillSummaryDTO:
    """
    分页账单汇总 DTO

    对应 SQL:
        SELECT COUNT(*) FROM fee_bills WHERE ...;
        SELECT * FROM fee_bills WHERE ... LIMIT :limit OFFSET :offset;
    """
    total_unpaid: float = 0.0
    unpaid_count: int = 0
    paid_count: int = 0
    overdue_count: int = 0
    bills: list[BillDTO] = field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total_count: int = 0
    total_pages: int = 0


# ── 映射函数 ─────────────────────────────────────────

def bill_to_dto(bill) -> BillDTO:
    """
    将领域实体 → DTO

    SQL:
        SELECT f.*, u.user_name, b.building_name, r.room_number
          FROM fee_bills f
          JOIN sys_users u ON f.user_id = u.user_id
          JOIN community_rooms r ON f.room_id = r.room_id
          JOIN community_buildings b ON r.building_id = b.building_id
         WHERE f.bill_id = :bill_id;
    """
    from ..domain.entities import Bill as DomainBill
    return BillDTO(
        bill_id=bill.bill_id,
        user_id=bill.user_id,
        period=bill.bill_period,
        property_fee=bill.property_fee,
        utility_fee=bill.utility_fee,
        parking_fee=bill.parking_fee,
        late_fee=bill.late_fee,
        total_amount=bill.total_amount,
        due_date=bill.due_date,
        status=bill.status.value if hasattr(bill.status, 'value') else bill.status,
        payment_time=bill.payment_time,
        receipt_no=bill.receipt_no,
        user_name=bill.user_name,
        building_name=bill.building_name,
        room_number=bill.room_number,
    )


def user_to_dto(user) -> UserDTO:
    """
    将用户实体 → DTO

    SQL:
        SELECT u.user_id, u.user_name AS name, u.role, u.phone,
               b.building_name AS building, r.room_number AS room
          FROM sys_users u
          LEFT JOIN community_buildings b ON u.building_id = b.building_id
          LEFT JOIN community_rooms r ON u.room_id = r.room_id
         WHERE u.user_id = :user_id;
    """
    return UserDTO(
        user_id=user.user_id,
        name=user.user_name,
        role=user.role.value if hasattr(user.role, 'value') else user.role,
        building=user.building_name if hasattr(user, 'building_name') else "",
        room=user.room_number if hasattr(user, 'room_number') else "",
        phone=user.phone,
    )


def payment_to_dto(payment) -> PaymentHistoryDTO:
    """
    将支付实体 → 支付历史 DTO

    SQL:
        SELECT p.*, u.user_name
          FROM fee_payments p
          JOIN sys_users u ON p.user_id = u.user_id
         WHERE p.payment_id = :payment_id;
    """
    return PaymentHistoryDTO(
        payment_id=payment.payment_id,
        bill_id=payment.bill_id,
        user_id=payment.user_id,
        user_name=getattr(payment, 'user_name', ''),
        pay_amount=payment.pay_amount,
        pay_method=payment.pay_method.value if hasattr(payment.pay_method, 'value') else payment.pay_method,
        pay_status=payment.pay_status.value if hasattr(payment.pay_status, 'value') else payment.pay_status,
        transaction_id=payment.transaction_id,
        receipt_no=payment.receipt_no,
        paid_at=payment.paid_at if payment.paid_at else "",
    )