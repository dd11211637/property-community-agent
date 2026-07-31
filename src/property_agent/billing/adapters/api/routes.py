"""
adapters/api/routes.py     FastAPI 路由

将 application 层用例暴露为 RESTful HTTP API。
每个路由端点标注了完整的调用链和等价 SQL 语句。

路由总览:
  GET    /api/bills                    账单列表（支持分页: ?page=1&page_size=20）
  GET    /api/bills/detail/{bill_id}   账单详情
  GET    /api/bills/export             账单导出（CSV 下载）
  POST   /api/bills/interpret          AI 账单解读
  POST   /api/bills/pay                单笔缴费
  POST   /api/bills/pay/batch          批量缴费
  POST   /api/bills/refund             退款（管理员）
  POST   /api/bills/cancel             取消账单（管理员）
  POST   /api/bills/generate           批量生成账单（管理员）
  POST   /api/bills/schedule-overdue   逾期自动检查
  GET    /api/bills/payments/history   支付历史
  GET    /api/bills/receipt/{no}       电子票据查询

调用链（以账单查询为例）:
    HTTP GET /api/bills?user_id=user_101&role=owner&page=1&page_size=20
      → routes.list_bills()
        → BillQueryUseCase.list_bills(user_id, role)
          → GetBillsByRole.execute(user_id, role)
            → BillRepository.find_by_user(user_id)
              → SQL: SELECT * FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;
            → 自动检查逾期: auto_check_overdue(bill)
            → 重算滞纳金: calculate_late_fee(bill)
            → 汇总统计: summarize_bills(bills)
          → 返回 BillSummaryDTO
        → 分页处理: 内存切片
        → 返回 PaginatedBillSummarySchema

调用链（以缴费为例）:
    HTTP POST /api/bills/pay {"bill_id": "bill_202607_101", "user_id": "user_101"}
      → routes.pay_bill()
        → PaymentUseCase.pay_single(bill_id, user_id)
          → PayBillCommand.execute(bill_id, user_id)
            → BillRepository.find_by_id(bill_id)              -- SELECT 前置校验
            → validate_payable(bill)                          -- 业务规则校验
            → PaymentGateway.process_payment(bill, user_id)   -- 模拟支付
            → transition_to(bill, PAID)                       -- 状态转换
            → BillRepository.save(bill)                       -- UPDATE 账单
            → PaymentRepository.save(payment)                 -- INSERT 支付记录
            → ReceiptRepository.save(receipt)                 -- INSERT 电子票据
            → UnitOfWork.commit()                             -- COMMIT 事务
          → 返回 PayResponseDTO
        → 返回 PayResponseSchema
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io
import math

from .schemas import (
    BillSchema, BillSummarySchema, InterpretRequest, InterpretResponseSchema,
    PayRequest, PayResponseSchema, BatchPayRequest, BatchPayResponseSchema,
    ReceiptSchema, UserSchema, CancelRequest, CancelResponseSchema,
    PaymentHistoryItemSchema, PaymentHistoryResponseSchema,
    RefundRequest, RefundResponseSchema,
    GenerateBillsRequest, GenerateBillsResponseSchema,
    OverdueCheckResponseSchema, PaginatedBillSummarySchema,
)
from ...application.use_cases import (
    BillQueryUseCase, PaymentUseCase, InterpretationUseCase, ReceiptUseCase,
    PaymentHistoryUseCase, ExportUseCase, CancelBillUseCase,
    RefundUseCase, BillGenerationUseCase, OverdueCheckUseCase,
)
from ...infrastructure.database import get_db
from ...infrastructure.repositories import (
    SqlAlchemyBillRepository,
    SqlAlchemyUserRepository,
    SqlAlchemyPaymentRepository,
    SqlAlchemyReceiptRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyRoomRepository,
)
from ...infrastructure.llm_client import HttpLLMClient
from ...infrastructure.payment_gateway import MockPaymentGateway

router = APIRouter(prefix="/api/bills", tags=["费用管理"])

# ═══════════════════════════════════════════════════════════════
# 依赖注入工厂
# 每个工厂函数为对应的 UseCase 组装所有依赖（Repository、Gateway、UoW）
# ═══════════════════════════════════════════════════════════════

def _bill_query_uc(db: Session = Depends(get_db)) -> BillQueryUseCase:
    """
    构造账单查询用例。

    注入依赖:
      - BillRepository:   SQLAlchemy 实现，查询 fee_bills 表
      - UserRepository:   SQLAlchemy 实现，查询 sys_users 表

    SQL:
        SELECT * FROM fee_bills WHERE ...;
        SELECT * FROM sys_users WHERE user_id = :user_id;
    """
    return BillQueryUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
        user_repo=SqlAlchemyUserRepository(db),
    )


def _payment_uc(db: Session = Depends(get_db)) -> PaymentUseCase:
    """
    构造缴费用例。

    注入依赖:
      - BillRepository:    更新账单状态
      - PaymentRepository: 创建支付记录
      - ReceiptRepository: 创建电子票据
      - PaymentGateway:    模拟支付处理
      - UnitOfWork:        事务管理

    SQL:
        BEGIN;
        UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
        INSERT INTO fee_payments (...) VALUES (...);
        INSERT INTO fee_receipts (...) VALUES (...);
        COMMIT;
    """
    return PaymentUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
        payment_repo=SqlAlchemyPaymentRepository(db),
        receipt_repo=SqlAlchemyReceiptRepository(db),
        payment_gateway=MockPaymentGateway(),
        uow=SqlAlchemyUnitOfWork(db),
    )


def _interpretation_uc(db: Session = Depends(get_db)) -> InterpretationUseCase:
    """
    构造账单解读用例。

    注入依赖:
      - BillRepository: 查询账单
      - UserRepository: 查询用户
      - LLMClient:      调用 LLM API（无 Key 时降级内置模板）

    SQL:
        SELECT * FROM fee_bills WHERE bill_id = :bill_id;
        SELECT user_name FROM sys_users WHERE user_id = :user_id;
    """
    return InterpretationUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
        user_repo=SqlAlchemyUserRepository(db),
        llm_client=HttpLLMClient(),
    )


def _receipt_uc(db: Session = Depends(get_db)) -> ReceiptUseCase:
    """
    构造票据查询用例。

    SQL:
        SELECT * FROM fee_receipts WHERE receipt_no = :receipt_no;
    """
    return ReceiptUseCase(
        receipt_repo=SqlAlchemyReceiptRepository(db),
    )


def _history_uc(db: Session = Depends(get_db)) -> PaymentHistoryUseCase:
    """
    构造支付历史查询用例。

    SQL:
        SELECT p.*, u.user_name FROM fee_payments p
        JOIN sys_users u ON p.user_id = u.user_id
        WHERE p.user_id = :user_id ORDER BY p.paid_at DESC;
    """
    return PaymentHistoryUseCase(
        payment_repo=SqlAlchemyPaymentRepository(db),
    )


def _export_uc(db: Session = Depends(get_db)) -> ExportUseCase:
    """
    构造账单导出用例。

    SQL:
        SELECT f.*, u.user_name, b.building_name, r.room_number
        FROM fee_bills f JOIN sys_users u, community_rooms r, community_buildings b
        ORDER BY f.bill_period DESC;
    """
    return ExportUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
    )


def _cancel_uc(db: Session = Depends(get_db)) -> CancelBillUseCase:
    """
    构造取消账单用例。

    SQL:
        UPDATE fee_bills SET status='CANCELLED', updated_at=NOW() WHERE bill_id=:bill_id;
    """
    return CancelBillUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
    )


def _refund_uc(db: Session = Depends(get_db)) -> RefundUseCase:
    """
    构造退款用例。

    SQL:
        BEGIN;
        UPDATE fee_bills SET status='UNPAID', payment_time=NULL, receipt_no=NULL WHERE bill_id=:id;
        UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:id;
        UPDATE fee_receipts SET is_valid=FALSE WHERE bill_id=:id;
        COMMIT;
    """
    return RefundUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
        payment_repo=SqlAlchemyPaymentRepository(db),
        receipt_repo=SqlAlchemyReceiptRepository(db),
        uow=SqlAlchemyUnitOfWork(db),
    )


def _generation_uc(db: Session = Depends(get_db)) -> BillGenerationUseCase:
    """
    构造账单生成用例。

    SQL:
        SELECT r.* FROM community_rooms r WHERE r.status = 'OCCUPIED';
        SELECT user_id FROM sys_users WHERE room_id = :room_id AND role = 'owner' LIMIT 1;
        INSERT INTO fee_bills (...) VALUES (...);
    """
    return BillGenerationUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
        user_repo=SqlAlchemyUserRepository(db),
        room_repo=SqlAlchemyRoomRepository(db),
    )


def _overdue_uc(db: Session = Depends(get_db)) -> OverdueCheckUseCase:
    """
    构造逾期检查用例。

    SQL:
        UPDATE fee_bills SET status='OVERDUE', updated_at=NOW()
        WHERE status='UNPAID' AND due_date < CURRENT_DATE;
    """
    return OverdueCheckUseCase(
        bill_repo=SqlAlchemyBillRepository(db),
    )


# ═══════════════════════════════════════════════════════════════
# 路由: 账单查询（支持分页）
# ═══════════════════════════════════════════════════════════════

@router.get("", response_model=PaginatedBillSummarySchema)
async def list_bills(
    user_id: str = Query("user_101", description="用户ID"),
    role: str = Query("owner", description="角色: owner/staff/admin"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    uc: BillQueryUseCase = Depends(_bill_query_uc),
):
    """
    获取账单列表及欠费汇总（含滞纳金自动重算），支持分页。

    调用链:
        HTTP GET /api/bills?user_id=user_101&role=owner&page=1&page_size=20
          → routes.list_bills()
            → BillQueryUseCase.list_bills(user_id, role)
              → GetBillsByRole.execute(user_id, role)
                → BillRepository.find_by_user(user_id) / find_by_building / find_all
                → auto_check_overdue(bill)  + calculate_late_fee(bill)
                → summarize_bills(bills)
            → 内存分页切片
            → 返回 PaginatedBillSummarySchema

    权限控制:
      - owner: 仅查询本人账单
      - staff: 查询负责楼栋的所有账单
      - admin: 全社区账单

    SQL:
        SELECT * FROM fee_bills
        WHERE user_id = :user_id       -- owner
           OR room_id IN (SELECT ...)  -- staff
        ORDER BY bill_period DESC
        LIMIT :page_size OFFSET :offset;

        SELECT COUNT(*) FROM fee_bills WHERE ...;  -- 总数
    """
    if role not in ("owner", "staff", "admin"):
        raise HTTPException(400, f"无效的角色: {role}")

    user = uc.get_user(user_id)
    if not user:
        raise HTTPException(404, f"用户 {user_id} 不存在")

    result = uc.list_bills(user_id, role)

    # 分页处理（内存切片）
    all_bills = result.bills
    total = len(all_bills)
    total_pages = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    end = start + page_size
    paged_bills = all_bills[start:end]

    # 将 BillDTO 转换为 BillSchema
    bill_schemas = [
        BillSchema(
            bill_id=b.bill_id,
            user_id=b.user_id,
            period=b.period,
            property_fee=b.property_fee,
            utility_fee=b.utility_fee,
            parking_fee=b.parking_fee,
            late_fee=b.late_fee,
            total_amount=b.total_amount,
            due_date=b.due_date,
            status=b.status,
            payment_time=b.payment_time,
            receipt_no=b.receipt_no,
            user_name=b.user_name,
            building_name=b.building_name,
            room_number=b.room_number,
        )
        for b in paged_bills
    ]

    return PaginatedBillSummarySchema(
        total_unpaid=result.total_unpaid,
        unpaid_count=result.unpaid_count,
        paid_count=result.paid_count,
        overdue_count=result.overdue_count,
        bills=bill_schemas,
        page=page,
        page_size=page_size,
        total_count=total,
        total_pages=total_pages,
    )


# ═══════════════════════════════════════════════════════════════
# 路由: 单笔账单详情
# ═══════════════════════════════════════════════════════════════

@router.get("/detail/{bill_id}", response_model=BillSchema)
async def get_bill_detail(
    bill_id: str,
    uc: BillQueryUseCase = Depends(_bill_query_uc),
):
    """
    获取单笔账单详情（含滞纳金自动重算）。

    调用链:
        HTTP GET /api/bills/detail/bill_202607_101
          → routes.get_bill_detail()
            → BillQueryUseCase.get_bill_detail(bill_id)
              → GetBillById.execute(bill_id)
                → BillRepository.find_by_id(bill_id)
                → auto_check_overdue(bill) + calculate_late_fee(bill)
              → bill_to_dto(bill)
            → 返回 BillSchema

    SQL:
        SELECT * FROM fee_bills WHERE bill_id = :bill_id;

        -- 滞纳金重算:
        SELECT property_fee, utility_fee, parking_fee, due_date, status
          FROM fee_bills WHERE bill_id = :bill_id;
        -- 应用层: calculate_late_fee(bill)
    """
    bill = uc.get_bill_detail(bill_id)
    if not bill:
        raise HTTPException(404, f"账单 {bill_id} 不存在")
    return BillSchema(
        bill_id=bill["bill_id"],
        user_id=bill["user_id"],
        period=bill["period"],
        property_fee=bill["property_fee"],
        utility_fee=bill["utility_fee"],
        parking_fee=bill["parking_fee"],
        late_fee=bill["late_fee"],
        total_amount=bill["total_amount"],
        due_date=bill["due_date"],
        status=bill["status"],
        payment_time=bill.get("payment_time"),
        receipt_no=bill.get("receipt_no"),
        user_name=bill.get("user_name", ""),
        building_name=bill.get("building_name", ""),
        room_number=bill.get("room_number", ""),
    )


# ═══════════════════════════════════════════════════════════════
# 路由: AI 账单解读
# ═══════════════════════════════════════════════════════════════

@router.post("/interpret", response_model=InterpretResponseSchema)
async def interpret_bill(
    body: InterpretRequest,
    uc: InterpretationUseCase = Depends(_interpretation_uc),
):
    """
    AI 账单解读：调用大模型 API 或内置模板，返回口语化解读 + 分层催缴提醒。

    调用链:
        HTTP POST /api/bills/interpret {"bill_id": "bill_202607_101", "user_id": "user_101"}
          → routes.interpret_bill()
            → InterpretationUseCase.interpret(bill_id, user_id)
              → GetBillById.execute(bill_id)          -- SELECT 账单
              → UpdateLateFeeCommand.execute(bill_id)  -- 重算滞纳金
              → GetUserById.execute(user_id)           -- SELECT 用户
              → LLMClient.interpret_bill(bill, name)   -- 调 LLM / 降级模板
                → determine_reminder_level(bill)       -- 判定催缴层级
                → generate_reminder_text(bill, level)  -- 生成催缴文案
              → 返回 InterpretResponseDTO
            → 返回 InterpretResponseSchema

    SQL:
        SELECT * FROM fee_bills WHERE bill_id = :bill_id;
        SELECT * FROM sys_users WHERE user_id = :user_id;
    """
    try:
        return await uc.interpret(body.bill_id, body.user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"解读服务异常: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 路由: 单笔缴费
# ═══════════════════════════════════════════════════════════════

@router.post("/pay", response_model=PayResponseSchema)
async def pay_bill(
    body: PayRequest,
    uc: PaymentUseCase = Depends(_payment_uc),
):
    """
    模拟一键缴费，自动生成电子票据。

    调用链:
        HTTP POST /api/bills/pay {"bill_id": "bill_202607_101", "user_id": "user_101"}
          → routes.pay_bill()
            → PaymentUseCase.pay_single(bill_id, user_id)
              → PayBillCommand.execute(bill_id, user_id)
                → BillRepository.find_by_id(bill_id)              -- SELECT 前置校验
                → validate_payable(bill)                          -- 业务规则校验
                → PaymentGateway.process_payment(bill, user_id)   -- 模拟支付
                → transition_to(bill, PAID)                       -- 状态转换
                → BillRepository.save(bill)                       -- UPDATE 账单
                → PaymentRepository.save(payment)                 -- INSERT 支付记录
                → ReceiptRepository.save(receipt)                 -- INSERT 电子票据
                → UnitOfWork.commit()                             -- COMMIT
              → 返回 PayResponseDTO
            → 返回 PayResponseSchema

    SQL:
        BEGIN;
        UPDATE fee_bills SET status='PAID', payment_time=NOW(), receipt_no=:rn WHERE bill_id=:id;
        INSERT INTO fee_payments (...) VALUES (...);
        INSERT INTO fee_receipts (...) VALUES (...);
        COMMIT;
    """
    try:
        return uc.pay_single(body.bill_id, body.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"缴费失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 路由: 批量缴费
# ═══════════════════════════════════════════════════════════════

@router.post("/pay/batch", response_model=BatchPayResponseSchema)
async def pay_bills_batch(
    body: BatchPayRequest,
    uc: PaymentUseCase = Depends(_payment_uc),
):
    """
    批量缴费，对每笔账单循环执行单笔缴费事务。

    调用链:
        HTTP POST /api/bills/pay/batch {"bill_ids": [...], "user_id": "user_101"}
          → routes.pay_bills_batch()
            → PaymentUseCase.pay_batch(bill_ids, user_id)
              → BatchPayCommand.execute(bill_ids, user_id)
                → 对每笔账单: PayBillCommand.execute(bill_id, user_id)
              → 返回 BatchPayResponseDTO
            → 返回 BatchPayResponseSchema

    SQL:
        对每笔账单循环执行:
        BEGIN;
        UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
        INSERT INTO fee_payments (...) VALUES (...);
        INSERT INTO fee_receipts (...) VALUES (...);
        COMMIT;
        -- 汇总: SELECT COUNT(*) FROM fee_payments WHERE paid_at >= :batch_start;
    """
    return uc.pay_batch(body.bill_ids, body.user_id)


# ═══════════════════════════════════════════════════════════════
# 路由: 退款（管理员专用）
# ═══════════════════════════════════════════════════════════════

@router.post("/refund", response_model=RefundResponseSchema)
async def refund_bill(
    body: RefundRequest,
    uc: RefundUseCase = Depends(_refund_uc),
):
    """
    退款（管理员专用），将已缴费账单退回到未缴费状态。

    调用链:
        HTTP POST /api/bills/refund {"bill_id": "bill_202607_101", "reason": "管理员退款"}
          → routes.refund_bill()
            → RefundUseCase.refund(bill_id, reason)
              → RefundBillCommand.execute(bill_id, reason)
                → BillRepository.find_by_id(bill_id)              -- SELECT 前置校验
                → validate_refundable(bill)                       -- 业务规则校验
                → BillRepository.save(bill → UNPAID)              -- UPDATE 账单
                → PaymentRepository.update_status(REFUNDED)       -- UPDATE 支付记录
                → ReceiptRepository.invalidate_by_bill_id()       -- UPDATE 票据作废
                → UnitOfWork.commit()                             -- COMMIT
              → 返回 RefundResponseDTO
            → 返回 RefundResponseSchema

    SQL:
        BEGIN;
        UPDATE fee_bills SET status='UNPAID', payment_time=NULL, receipt_no=NULL WHERE bill_id=:id;
        UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:id AND pay_status='SUCCESS';
        UPDATE fee_receipts SET is_valid=FALSE WHERE bill_id=:id;
        COMMIT;
    """
    try:
        return uc.refund(body.bill_id, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"退款失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 路由: 取消账单（管理员专用）
# ═══════════════════════════════════════════════════════════════

@router.post("/cancel", response_model=CancelResponseSchema)
async def cancel_bill(
    body: CancelRequest,
    uc: CancelBillUseCase = Depends(_cancel_uc),
):
    """
    取消账单（管理员专用），将未缴费/逾期账单作废。

    调用链:
        HTTP POST /api/bills/cancel {"bill_id": "bill_202607_102", "reason": "数据错误"}
          → routes.cancel_bill()
            → CancelBillUseCase.cancel(bill_id, reason)
              → CancelBillCommand.execute(bill_id, reason)
                → BillRepository.find_by_id(bill_id)    -- SELECT 前置校验
                → validate_cancellable(bill)             -- 业务规则校验
                → transition_to(bill, CANCELLED)         -- 状态转换
                → BillRepository.save(bill)              -- UPDATE 账单
              → 返回 CancelResponseDTO
            → 返回 CancelResponseSchema

    SQL:
        UPDATE fee_bills
           SET status = 'CANCELLED',
               updated_at = NOW()
         WHERE bill_id = :bill_id;
    """
    try:
        return uc.cancel(body.bill_id, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ═══════════════════════════════════════════════════════════════
# 路由: 批量生成账单（管理员专用）
# ═══════════════════════════════════════════════════════════════

@router.post("/generate", response_model=GenerateBillsResponseSchema)
async def generate_bills(
    body: GenerateBillsRequest,
    uc: BillGenerationUseCase = Depends(_generation_uc),
):
    """
    批量生成账单（管理员专用），为指定账期（默认上月）批量生成所有活跃房号的账单。

    调用链:
        HTTP POST /api/bills/generate {"period": "2026-07"}
          → routes.generate_bills()
            → BillGenerationUseCase.generate(period)
              → GenerateBillsCommand.execute(period)
                → RoomRepository.find_active_rooms()                  -- SELECT 活跃房号
                → UserRepository.find_owner_by_room(room_id)          -- SELECT 业主
                → BillRepository.exists_by_user_and_period()          -- 去重检查
                → calculate_bill_fees(room)                           -- 计算费用
                → generate_bill_id(room_id, period)                   -- 生成ID
                → calculate_due_date(period)                          -- 计算到期日
                → BillRepository.create(bill)                         -- INSERT 账单
              → 返回 GenerateBillsResponseDTO
            → 返回 GenerateBillsResponseSchema

    SQL:
        SELECT r.* FROM community_rooms r WHERE r.status = 'OCCUPIED';
        SELECT user_id FROM sys_users WHERE room_id = :room_id AND role = 'owner' LIMIT 1;
        SELECT COUNT(*) FROM fee_bills WHERE user_id = :user_id AND bill_period = :period;
        INSERT INTO fee_bills (...) VALUES (...);  -- 每间房一条
    """
    try:
        return uc.generate(body.period)
    except Exception as e:
        raise HTTPException(500, f"账单生成失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 路由: 逾期自动检查
# ═══════════════════════════════════════════════════════════════

@router.post("/schedule-overdue", response_model=OverdueCheckResponseSchema)
async def schedule_overdue_check(
    uc: OverdueCheckUseCase = Depends(_overdue_uc),
):
    """
    逾期自动检查调度，扫描所有 UNPAID 账单，将已过期的自动转为 OVERDUE。

    调用链:
        HTTP POST /api/bills/schedule-overdue
          → routes.schedule_overdue_check()
            → OverdueCheckUseCase.check()
              → CheckOverdueCommand.execute()
                → BillRepository.find_unpaid_before_date(today)   -- SELECT 逾期账单
                → BillRepository.bulk_update_status(ids, OVERDUE) -- UPDATE 批量更新
              → 返回 CheckOverdueResponseDTO
            → 返回 OverdueCheckResponseSchema

    SQL:
        SELECT * FROM fee_bills WHERE status = 'UNPAID' AND due_date < CURRENT_DATE;
        UPDATE fee_bills SET status = 'OVERDUE', updated_at = NOW()
         WHERE bill_id IN (:bill_ids);
    """
    return uc.check()


# ═══════════════════════════════════════════════════════════════
# 路由: 电子票据查询
# ═══════════════════════════════════════════════════════════════

@router.get("/receipt/{receipt_no}", response_model=ReceiptSchema)
async def get_receipt(
    receipt_no: str,
    uc: ReceiptUseCase = Depends(_receipt_uc),
):
    """
    获取电子票据详情。

    调用链:
        HTTP GET /api/bills/receipt/REC_20260728_101
          → routes.get_receipt()
            → ReceiptUseCase.get_receipt(receipt_no)
              → GetReceiptByNo.execute(receipt_no)
                → ReceiptRepository.find_by_no(receipt_no)
                  → SQL: SELECT r.*, u.user_name, b.building_name, rm.room_number, p.paid_at
                         FROM fee_receipts r JOIN sys_users u, fee_bills f, community_rooms rm,
                         community_buildings b, fee_payments p WHERE r.receipt_no = :no;
              → 返回 ReceiptDTO
            → 返回 ReceiptSchema

    SQL:
        SELECT r.*, u.user_name, b.building_name, rm.room_number, p.paid_at AS payment_time
          FROM fee_receipts r
          JOIN sys_users u ON r.user_id = u.user_id
          JOIN fee_bills f ON r.bill_id = f.bill_id
          JOIN community_rooms rm ON f.room_id = rm.room_id
          JOIN community_buildings b ON rm.building_id = b.building_id
          JOIN fee_payments p ON r.payment_id = p.payment_id
         WHERE r.receipt_no = :receipt_no;
    """
    receipt = uc.get_receipt(receipt_no)
    if not receipt:
        raise HTTPException(404, f"票据 {receipt_no} 不存在")
    return receipt


# ═══════════════════════════════════════════════════════════════
# 路由: 支付历史查询
# ═══════════════════════════════════════════════════════════════

@router.get("/payments/history", response_model=PaymentHistoryResponseSchema)
async def get_payment_history(
    user_id: str = Query("user_101", description="用户ID"),
    role: str = Query("owner", description="角色: owner/admin"),
    uc: PaymentHistoryUseCase = Depends(_history_uc),
):
    """
    获取支付历史记录。

    调用链:
        HTTP GET /api/bills/payments/history?user_id=user_101&role=owner
          → routes.get_payment_history()
            → PaymentHistoryUseCase.get_user_history(user_id) / get_all_history()
              → GetPaymentHistoryByUser.execute(user_id)
                → PaymentRepository.find_by_user(user_id)
                  → SQL: SELECT p.*, u.user_name FROM fee_payments p
                         JOIN sys_users u ON p.user_id = u.user_id
                         WHERE p.user_id = :user_id ORDER BY p.paid_at DESC;
              → 返回 PaymentHistoryResponseDTO
            → 转换为 PaymentHistoryResponseSchema

    SQL (owner):
        SELECT p.*, u.user_name
          FROM fee_payments p
          JOIN sys_users u ON p.user_id = u.user_id
         WHERE p.user_id = :user_id
         ORDER BY p.paid_at DESC;

    SQL (admin):
        SELECT * FROM fee_payments ORDER BY paid_at DESC;
    """
    try:
        if role == "admin":
            result = uc.get_all_history()
        else:
            result = uc.get_user_history(user_id)

        # 将 DTO 转换为 Schema
        payments = [
            PaymentHistoryItemSchema(
                payment_id=p.payment_id,
                bill_id=p.bill_id,
                user_id=p.user_id,
                user_name=p.user_name,
                pay_amount=p.pay_amount,
                pay_method=p.pay_method,
                pay_status=p.pay_status,
                transaction_id=p.transaction_id,
                receipt_no=p.receipt_no,
                paid_at=p.paid_at,
            )
            for p in result.payments
        ]
        return PaymentHistoryResponseSchema(
            payments=payments,
            total_count=result.total_count,
            total_amount=result.total_amount,
        )
    except Exception as e:
        raise HTTPException(500, f"查询支付历史失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 路由: 滞纳金重算
# ═══════════════════════════════════════════════════════════════

@router.post("/recalculate-late-fee")
async def recalculate_late_fee(
    bill_id: str = Query(..., description="账单ID"),
    uc: PaymentUseCase = Depends(_payment_uc),
):
    """
    重新计算指定账单的滞纳金。

    调用链:
        HTTP POST /api/bills/recalculate-late-fee?bill_id=bill_202607_101
          → routes.recalculate_late_fee()
            → PaymentUseCase.recalculate_late_fee(bill_id)
              → UpdateLateFeeCommand.execute(bill_id)
                → BillRepository.find_by_id(bill_id)
                  → SQL: SELECT * FROM fee_bills WHERE bill_id = :bill_id;
                → calculate_late_fee(bill)
                → BillRepository.update_late_fee(bill_id, new_fee)
                  → SQL: UPDATE fee_bills
                         SET late_fee = :late_fee,
                             total_amount = property_fee + utility_fee + parking_fee + :late_fee,
                             updated_at = NOW()
                         WHERE bill_id = :bill_id;
              → 返回 new_late_fee (float)

    SQL:
        UPDATE fee_bills
           SET late_fee = :late_fee,
               total_amount = property_fee + utility_fee + parking_fee + :late_fee,
               updated_at = NOW()
         WHERE bill_id = :bill_id;
    """
    try:
        new_fee = uc.recalculate_late_fee(bill_id)
        return {
            "success": True,
            "bill_id": bill_id,
            "new_late_fee": new_fee,
            "message": f"滞纳金已重新计算: {new_fee:.2f} 元",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"滞纳金重算失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 路由: 账单导出（CSV）
# ═══════════════════════════════════════════════════════════════

@router.get("/export")
async def export_bills(
    user_id: str = Query("user_101", description="用户ID"),
    role: str = Query("owner", description="角色: owner/staff/admin"),
    building_id: str = Query("", description="楼栋ID（staff 角色必填）"),
    uc: ExportUseCase = Depends(_export_uc),
):
    """
    导出账单为 CSV 文件下载。

    调用链:
        HTTP GET /api/bills/export?user_id=user_101&role=owner
        HTTP GET /api/bills/export?user_id=staff_201&role=staff&building_id=bld_A
          → routes.export_bills()
            → ExportUseCase.export_csv(user_id, role, building_id)
              → ExportBillsCommand.execute(user_id, role, building_id)
                → owner:  BillRepository.find_by_user(user_id)
                → staff:  BillRepository.find_by_building(building_id)
                → admin:  BillRepository.find_all()
                → bills_to_csv(bills)  -- 生成 CSV 内容
              → 返回 ExportResponseDTO
            → StreamingResponse 返回 CSV 文件下载

    SQL:
        -- owner
        SELECT ... FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;
        -- staff (按楼栋)
        SELECT f.* FROM fee_bills f
        JOIN community_rooms r ON f.room_id = r.room_id
        WHERE r.building_id = :building_id ORDER BY f.bill_period DESC;
        -- admin
        SELECT ... FROM fee_bills ORDER BY bill_period DESC;
    """
    try:
        result = uc.export_csv(user_id, role, building_id)
        return StreamingResponse(
            io.StringIO(result.csv_content),
            media_type="text/csv; charset=utf-8-sig",
            headers={
                "Content-Disposition": f"attachment; filename={result.filename}",
            },
        )
    except Exception as e:
        raise HTTPException(500, f"导出失败: {str(e)}")

# ═══════════════════════════════════════════════════════════════
# 路由: 用户信息查询（跨模块公共）
# ═══════════════════════════════════════════════════════════════

_user_router = APIRouter(prefix="/api/users", tags=["用户管理"])

@_user_router.get("/{user_id}")
async def get_user_info(
    user_id: str,
    db: Session = Depends(get_db),
):
    """
    获取用户信息。

    调用链:
        HTTP GET /api/users/user_101
          → get_user_info(user_id)
            → SqlAlchemyUserRepository.find_by_id(user_id)
              → SQL: SELECT u.*, b.building_name, r.room_number
                     FROM sys_users u
                     LEFT JOIN community_buildings b ON u.building_id = b.building_id
                     LEFT JOIN community_rooms r ON u.room_id = r.room_id
                     WHERE u.user_id = :user_id;

    SQL:
        SELECT u.user_id, u.user_name, u.role, u.phone,
               b.building_name, r.room_number
          FROM sys_users u
          LEFT JOIN community_buildings b ON u.building_id = b.building_id
          LEFT JOIN community_rooms r ON u.room_id = r.room_id
         WHERE u.user_id = :user_id;
    """
    from ...application.dtos import user_to_dto
    from dataclasses import asdict

    user_repo = SqlAlchemyUserRepository(db)
    user = user_repo.find_by_id(user_id)
    if not user:
        raise HTTPException(404, f"用户 {user_id} 不存在")
    return asdict(user_to_dto(user))

# 将用户路由也注册到 billing 路由中，外部统一挂载
router.include_router(_user_router)