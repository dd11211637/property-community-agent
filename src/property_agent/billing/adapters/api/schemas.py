"""
adapters/api/schemas.py     FastAPI Pydantic Schemas

定义 API 请求 / 响应的数据模型，每个 Schema 对应一个 API 端点的输入/输出。
与 application/dtos.py 中的 DTO 一一对应，在路由层进行 DTO → Schema 转换。

调用链（以缴费为例）:
    HTTP POST /api/bills/pay {"bill_id": "bill_202607_101", "user_id": "user_101"}
      → PayRequest (Pydantic validation)
        → routes.pay_bill(body)
          → PaymentUseCase.pay_single(body.bill_id, body.user_id)
            → PayResponseDTO (dataclass)
          → PayResponseSchema (Pydantic serialization)
        → JSON Response

Schema 清单:
  请求:
    InterpretRequest             POST /api/bills/interpret
    PayRequest                   POST /api/bills/pay
    BatchPayRequest              POST /api/bills/pay/batch
    CancelRequest                POST /api/bills/cancel
    RefundRequest                POST /api/bills/refund
    GenerateBillsRequest         POST /api/bills/generate

  响应:
    BillSchema                   GET /api/bills, GET /api/bills/detail/{bill_id}
    BillSummarySchema            GET /api/bills（汇总信息）
    PaginatedBillSummarySchema   GET /api/bills?page=1&page_size=20（分页）
    FeeItemSchema                费用明细
    InterpretResponseSchema      POST /api/bills/interpret
    PayResponseSchema            POST /api/bills/pay
    BatchPayResponseSchema       POST /api/bills/pay/batch
    CancelResponseSchema         POST /api/bills/cancel
    RefundResponseSchema         POST /api/bills/refund
    GenerateBillsResponseSchema  POST /api/bills/generate
    OverdueCheckResponseSchema   POST /api/bills/schedule-overdue
    ReceiptSchema                GET /api/bills/receipt/{receipt_no}
    PaymentHistoryItemSchema     GET /api/bills/payments/history
    PaymentHistoryResponseSchema GET /api/bills/payments/history
    UserSchema                   GET /api/users/{user_id}
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# 请求 Schema
# ═══════════════════════════════════════════════════════════════

class InterpretRequest(BaseModel):
    """
    账单解读请求

    对应 API: POST /api/bills/interpret

    调用链:
        HTTP POST /api/bills/interpret
          → InterpretRequest (Pydantic 校验)
          → InterpretationUseCase.interpret(bill_id, user_id)
            → SQL: SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            → SQL: SELECT user_name FROM sys_users WHERE user_id = :user_id;
    """
    bill_id: str = Field(..., description="账单ID")
    user_id: str = Field(default="user_101", description="用户ID")


class PayRequest(BaseModel):
    """
    单笔缴费请求

    对应 API: POST /api/bills/pay

    调用链:
        HTTP POST /api/bills/pay
          → PayRequest (Pydantic 校验)
          → PaymentUseCase.pay_single(bill_id, user_id)
            → PayBillCommand.execute(bill_id, user_id)
              → SQL: BEGIN;
              → SQL: UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:bill_id;
              → SQL: INSERT INTO fee_payments (...) VALUES (...);
              → SQL: INSERT INTO fee_receipts (...) VALUES (...);
              → SQL: COMMIT;
    """
    bill_id: str = Field(..., description="账单ID")
    user_id: str = Field(default="user_101", description="用户ID")


class BatchPayRequest(BaseModel):
    """
    批量缴费请求

    对应 API: POST /api/bills/pay/batch

    调用链:
        HTTP POST /api/bills/pay/batch
          → BatchPayRequest (Pydantic 校验)
          → PaymentUseCase.pay_batch(bill_ids, user_id)
            → 对每笔账单循环执行 PayBillCommand
    """
    bill_ids: list[str] = Field(..., description="账单ID列表")
    user_id: str = Field(default="user_101", description="用户ID")


class CancelRequest(BaseModel):
    """
    取消账单请求（管理员专用）

    对应 API: POST /api/bills/cancel

    调用链:
        HTTP POST /api/bills/cancel
          → CancelRequest (Pydantic 校验)
          → CancelBillUseCase.cancel(bill_id, reason)
            → SQL: UPDATE fee_bills SET status='CANCELLED', updated_at=NOW() WHERE bill_id=:bill_id;
    """
    bill_id: str = Field(..., description="账单ID")
    reason: str = Field(default="管理员手动作废", description="作废原因")


class RefundRequest(BaseModel):
    """
    退款请求（管理员专用）

    对应 API: POST /api/bills/refund

    调用链:
        HTTP POST /api/bills/refund
          → RefundRequest (Pydantic 校验)
          → RefundUseCase.refund(bill_id, reason)
            → SQL: BEGIN;
            → SQL: UPDATE fee_bills SET status='UNPAID', payment_time=NULL, receipt_no=NULL WHERE bill_id=:id;
            → SQL: UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:id;
            → SQL: UPDATE fee_receipts SET is_valid=FALSE WHERE bill_id=:id;
            → SQL: COMMIT;
    """
    bill_id: str = Field(..., description="账单ID")
    reason: str = Field(default="管理员退款", description="退款原因")


class GenerateBillsRequest(BaseModel):
    """
    账单生成请求（管理员专用）

    对应 API: POST /api/bills/generate

    调用链:
        HTTP POST /api/bills/generate
          → GenerateBillsRequest (Pydantic 校验)
          → BillGenerationUseCase.generate(period)
            → SQL: INSERT INTO fee_bills (...) VALUES (...);  -- 每间房一条
    """
    period: str = Field(default="", description="账期 YYYY-MM，为空则自动取上月")


# ═══════════════════════════════════════════════════════════════
# 响应 Schema
# ═══════════════════════════════════════════════════════════════

class FeeItemSchema(BaseModel):
    """
    费用明细

    对应 SQL:
        SELECT property_fee, utility_fee, parking_fee, late_fee
          FROM fee_bills WHERE bill_id = :bill_id;
    """
    property_fee: float = 0.0
    utility_fee: float = 0.0
    parking_fee: float = 0.0
    late_fee: float = 0.0


class BillSchema(BaseModel):
    """
    账单响应

    对应 API: GET /api/bills, GET /api/bills/detail/{bill_id}

    对应 SQL:
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


class BillSummarySchema(BaseModel):
    """
    账单汇总响应（不含分页信息）

    对应 API: GET /api/bills（无分页参数时）

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
    bills: list[BillSchema] = []


class PaginatedBillSummarySchema(BillSummarySchema):
    """
    分页账单汇总响应

    对应 API: GET /api/bills?page=1&page_size=20

    继承 BillSummarySchema 的所有汇总字段，额外增加分页信息。

    对应 SQL:
        SELECT COUNT(*) FROM fee_bills WHERE ...;                    -- 总数
        SELECT * FROM fee_bills WHERE ... LIMIT :limit OFFSET :offset;  -- 分页数据
    """
    page: int = 1
    page_size: int = 20
    total_count: int = 0
    total_pages: int = 0


class InterpretResponseSchema(BaseModel):
    """
    账单解读响应

    对应 API: POST /api/bills/interpret

    调用链:
        InterpretationUseCase.interpret()
          → LLMClient.interpret_bill() / 降级模板
          → 返回 InterpretResponseDTO
          → 转换为 InterpretResponseSchema
    """
    bill_id: str
    interpretation: str
    reminder_level: str
    reminder_text: str


class PayResponseSchema(BaseModel):
    """
    缴费响应

    对应 API: POST /api/bills/pay

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


class BatchPayResponseSchema(BaseModel):
    """
    批量缴费响应

    对应 API: POST /api/bills/pay/batch

    调用链:
        PaymentUseCase.pay_batch() → BatchPayCommand.execute() → 返回 BatchPayResponseDTO
    """
    success_count: int
    failed_count: int
    results: list[PayResponseSchema] = []
    total_paid: float = 0.0


class CancelResponseSchema(BaseModel):
    """
    取消账单响应

    对应 API: POST /api/bills/cancel

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


class RefundResponseSchema(BaseModel):
    """
    退款响应

    对应 API: POST /api/bills/refund

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


class GenerateBillsResponseSchema(BaseModel):
    """
    账单生成响应

    对应 API: POST /api/bills/generate

    对应 SQL:
        INSERT INTO fee_bills (...) VALUES (...);  -- 每间房一条
    """
    generated_count: int
    skipped_count: int
    period: str
    due_date: str


class OverdueCheckResponseSchema(BaseModel):
    """
    逾期检查响应

    对应 API: POST /api/bills/schedule-overdue

    对应 SQL:
        UPDATE fee_bills SET status='OVERDUE', updated_at=NOW()
         WHERE status='UNPAID' AND due_date < CURRENT_DATE;
    """
    updated_count: int
    total_checked: int
    check_time: str


class ReceiptSchema(BaseModel):
    """
    电子票据响应

    对应 API: GET /api/bills/receipt/{receipt_no}

    对应 SQL:
        SELECT r.*, u.user_name, b.building_name, rm.room_number, p.paid_at AS payment_time
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
    items: FeeItemSchema
    total_amount: float
    payment_time: str
    issue_time: str
    note: str = "电子票据，与纸质票据具有同等效力"


class UserSchema(BaseModel):
    """
    用户信息响应

    对应 API: GET /api/users/{user_id}

    对应 SQL:
        SELECT u.user_id, u.user_name, u.role, u.phone,
               b.building_name, r.room_number
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


class PaymentHistoryItemSchema(BaseModel):
    """
    支付记录响应

    对应 API: GET /api/bills/payments/history

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


class PaymentHistoryResponseSchema(BaseModel):
    """
    支付历史响应

    对应 API: GET /api/bills/payments/history

    调用链:
        PaymentHistoryUseCase.get_user_history() / get_all_history()
          → GetPaymentHistoryByUser.execute() / GetPaymentHistoryAll.execute()
          → PaymentRepository.find_by_user() / find_all()
          → 返回 PaymentHistoryResponseDTO
          → 转换为 PaymentHistoryResponseSchema
    """
    payments: list[PaymentHistoryItemSchema] = []
    total_count: int = 0
    total_amount: float = 0.0