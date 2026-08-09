"""
application/use_cases.py     用例编排

应用层核心：组合领域实体、业务规则、查询、命令和外部服务端口，
实现完整的业务用例。
每个方法标注了对应的等价 SQL 语句。
"""
from __future__ import annotations
from typing import Optional

from .ports import (
    BillRepository, UserRepository, PaymentRepository,
    ReceiptRepository, LLMClient, PaymentGateway, UnitOfWork,
    RoomRepository,
)
from .queries import (
    GetBillsByRole, GetBillById, GetUserById, GetReceiptByNo,
    GetPaymentHistoryByUser, GetPaymentHistoryAll,
)
from .commands import (
    PayBillCommand, BatchPayCommand, UpdateLateFeeCommand,
    CancelBillCommand, ExportBillsCommand, RefundBillCommand,
    GenerateBillsCommand, CheckOverdueCommand,
)
from .dtos import (
    BillSummaryDTO, InterpretResponseDTO, PayResponseDTO,
    ReceiptDTO, BatchPayResponseDTO, PaymentHistoryResponseDTO,
    ExportResponseDTO, CancelResponseDTO, RefundResponseDTO,
    GenerateBillsResponseDTO, CheckOverdueResponseDTO,
)
from ..domain.business_rules import (
    determine_reminder_level, generate_reminder_text,
    calculate_late_fee,
)


class BillQueryUseCase:
    """
    账单查询用例

    入口: GET /api/bills
    GET /api/bills/{bill_id}

    SQL:
        SELECT * FROM fee_bills
        WHERE user_id = :user_id       -- owner
           OR room_id IN (SELECT ...)  -- staff
        ORDER BY bill_period DESC;     -- admin (无过滤)
    """

    def __init__(
        self,
        bill_repo: BillRepository,
        user_repo: UserRepository,
    ):
        self._get_bills = GetBillsByRole(bill_repo, user_repo)
        self._get_bill_by_id = GetBillById(bill_repo)
        self._get_user = GetUserById(user_repo)

    def list_bills(self, user_id: str, role: str) -> BillSummaryDTO:
        """
        按角色查询账单列表（含滞纳金自动重算）

        SQL:
            SELECT * FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;
        """
        return self._get_bills.execute(user_id, role)

    def get_bill_detail(self, bill_id: str) -> Optional[dict]:
        """
        查询单笔账单（含滞纳金自动重算）

        SQL:
            SELECT * FROM fee_bills WHERE bill_id = :bill_id;
        """
        bill = self._get_bill_by_id.execute(bill_id)
        if not bill:
            return None
        from .dtos import bill_to_dto
        from dataclasses import asdict
        return asdict(bill_to_dto(bill))

    def get_user(self, user_id: str) -> Optional[dict]:
        """
        查询用户

        SQL:
            SELECT * FROM sys_users WHERE user_id = :user_id;
        """
        dto = self._get_user.execute(user_id)
        if not dto:
            return None
        from dataclasses import asdict
        return asdict(dto)


class PaymentUseCase:
    """
    缴费用例

    入口: POST /api/bills/pay
    POST /api/bills/pay/batch

    SQL:
        BEGIN;
        UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
        INSERT INTO fee_payments (...) VALUES (...);
        INSERT INTO fee_receipts (...) VALUES (...);
        COMMIT;
    """

    def __init__(
        self,
        bill_repo: BillRepository,
        payment_repo: PaymentRepository,
        receipt_repo: ReceiptRepository,
        payment_gateway: PaymentGateway,
        uow: UnitOfWork,
    ):
        self._pay_cmd = PayBillCommand(
            bill_repo, payment_repo, receipt_repo, payment_gateway, uow
        )
        self._batch_pay_cmd = BatchPayCommand(self._pay_cmd)
        self._update_late_fee_cmd = UpdateLateFeeCommand(bill_repo)

    def pay_single(self, bill_id: str, user_id: str) -> PayResponseDTO:
        """
        单笔缴费

        SQL:
            BEGIN;
            UPDATE fee_bills SET status='PAID', payment_time=NOW(), receipt_no=:rn WHERE bill_id=:id;
            INSERT INTO fee_payments (...) VALUES (...);
            INSERT INTO fee_receipts (...) VALUES (...);
            COMMIT;
        """
        result = self._pay_cmd.execute(bill_id, user_id)
        return PayResponseDTO(**result)

    def pay_batch(self, bill_ids: list[str], user_id: str) -> BatchPayResponseDTO:
        """
        批量缴费

        SQL:
            对每笔账单循环执行单笔缴费的事务。
        """
        result = self._batch_pay_cmd.execute(bill_ids, user_id)
        return BatchPayResponseDTO(**result)

    def recalculate_late_fee(self, bill_id: str) -> float:
        """
        重新计算滞纳金

        SQL:
            UPDATE fee_bills SET late_fee=:late_fee, total_amount=... WHERE bill_id=:id;
        """
        return self._update_late_fee_cmd.execute(bill_id)


class InterpretationUseCase:
    """
    账单解读用例

    入口: POST /api/bills/interpret

    流程:
        1. 查询账单 (SELECT * FROM fee_bills WHERE bill_id = :id)
        2. 重新计算滞纳金
        3. 调用 LLM 或内置模板解读
        4. 返回解读 + 催缴提醒
    """

    def __init__(
        self,
        bill_repo: BillRepository,
        user_repo: UserRepository,
        llm_client: LLMClient,
    ):
        self._get_bill = GetBillById(bill_repo)
        self._get_user = GetUserById(user_repo)
        self._llm_client = llm_client
        self._update_late_fee = UpdateLateFeeCommand(bill_repo)

    async def interpret(self, bill_id: str, user_id: str) -> InterpretResponseDTO:
        # 1. 查询账单
        bill = self._get_bill.execute(bill_id)
        if not bill:
            raise ValueError(f"账单 {bill_id} 不存在")

        # 2. 更新滞纳金
        try:
            new_fee = self._update_late_fee.execute(bill_id)
            bill.late_fee = new_fee
        except Exception:
            pass  # 滞纳金计算失败不影响解读

        # 3. 查询用户
        user_name = "业主"
        user = self._get_user.execute(user_id)
        if user:
            user_name = user["name"]

        # 4. LLM 解读
        interpretation, rem_level, rem_text = await self._llm_client.interpret_bill(
            bill, user_name
        )

        return InterpretResponseDTO(
            bill_id=bill_id,
            interpretation=interpretation,
            reminder_level=rem_level.value if hasattr(rem_level, 'value') else rem_level,
            reminder_text=rem_text,
        )


class ReceiptUseCase:
    """
    票据查询用例

    入口: GET /api/bills/receipt/{receipt_no}

    SQL:
        SELECT * FROM fee_receipts WHERE receipt_no = :receipt_no;
    """

    def __init__(self, receipt_repo: ReceiptRepository):
        self._get_receipt = GetReceiptByNo(receipt_repo)

    def get_receipt(self, receipt_no: str) -> Optional[ReceiptDTO]:
        return self._get_receipt.execute(receipt_no)


# ── 新增: 支付历史用例 ────────────────────────────────

class PaymentHistoryUseCase:
    """
    支付历史查询用例

    入口: GET /api/bills/payments/history

    SQL:
        SELECT p.*, u.user_name
          FROM fee_payments p
          JOIN sys_users u ON p.user_id = u.user_id
         WHERE p.user_id = :user_id
         ORDER BY p.paid_at DESC;
    """

    def __init__(self, payment_repo: PaymentRepository):
        self._get_history = GetPaymentHistoryByUser(payment_repo)
        self._get_all_history = GetPaymentHistoryAll(payment_repo)

    def get_user_history(self, user_id: str) -> PaymentHistoryResponseDTO:
        """查询用户的支付历史"""
        return self._get_history.execute(user_id)

    def get_all_history(self) -> PaymentHistoryResponseDTO:
        """查询所有支付历史（管理员用）"""
        return self._get_all_history.execute()


# ── 新增: 账单导出用例 ────────────────────────────────

class ExportUseCase:
    """
    账单导出用例

    入口: GET /api/bills/export

    SQL:
        -- owner
        SELECT ... FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;
        -- staff
        SELECT f.* FROM fee_bills f JOIN community_rooms r ON f.room_id = r.room_id
        WHERE r.building_id = :building_id ORDER BY f.bill_period DESC;
        -- admin
        SELECT ... FROM fee_bills ORDER BY bill_period DESC;
    """

    def __init__(self, bill_repo: BillRepository):
        self._export_cmd = ExportBillsCommand(bill_repo)

    def export_csv(self, user_id: str, role: str, building_id: str = "") -> ExportResponseDTO:
        """
        导出账单为 CSV

        SQL:
            SELECT ... FROM fee_bills ... ORDER BY bill_period DESC;
        """
        result = self._export_cmd.execute(user_id, role, building_id)
        return ExportResponseDTO(**result)


# ── 新增: 取消账单用例 ────────────────────────────────

class CancelBillUseCase:
    """
    取消账单用例（管理员专用）

    入口: POST /api/bills/cancel

    SQL:
        UPDATE fee_bills
           SET status = 'CANCELLED',
               updated_at = NOW()
         WHERE bill_id = :bill_id;
    """

    def __init__(self, bill_repo: BillRepository):
        self._cancel_cmd = CancelBillCommand(bill_repo)

    def cancel(self, bill_id: str, reason: str = "管理员手动作废") -> CancelResponseDTO:
        result = self._cancel_cmd.execute(bill_id, reason)
        return CancelResponseDTO(**result)


# ── 新增: 退款用例 ────────────────────────────────────

class RefundUseCase:
    """
    退款用例（管理员专用）

    入口: POST /api/bills/refund

    SQL:
        BEGIN;
        UPDATE fee_bills SET status='UNPAID', payment_time=NULL, receipt_no=NULL WHERE bill_id=:id;
        UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:id;
        UPDATE fee_receipts SET is_valid=FALSE WHERE bill_id=:id;
        COMMIT;
    """

    def __init__(
        self,
        bill_repo: BillRepository,
        payment_repo: PaymentRepository,
        receipt_repo: ReceiptRepository,
        uow: UnitOfWork,
    ):
        self._refund_cmd = RefundBillCommand(
            bill_repo, payment_repo, receipt_repo, uow
        )

    def refund(self, bill_id: str, reason: str = "管理员退款") -> RefundResponseDTO:
        result = self._refund_cmd.execute(bill_id, reason)
        return RefundResponseDTO(**result)


# ── 新增: 账单生成用例 ────────────────────────────────

class BillGenerationUseCase:
    """
    账单自动生成用例（管理员专用）

    入口: POST /api/bills/generate

    为指定账期（默认上月）批量生成所有活跃房号的账单。

    SQL:
        INSERT INTO fee_bills (...) VALUES (...)  -- 每间房一条
    """

    def __init__(
        self,
        bill_repo: BillRepository,
        user_repo: UserRepository,
        room_repo: RoomRepository,
    ):
        self._generate_cmd = GenerateBillsCommand(
            bill_repo, user_repo, room_repo
        )

    def generate(self, period: str = "") -> GenerateBillsResponseDTO:
        """
        批量生成账单。
        period 为空时自动取上月。
        """
        from datetime import date, timedelta
        if not period:
            today = date.today()
            first_of_month = today.replace(day=1)
            last_month = first_of_month - timedelta(days=1)
            period = last_month.strftime("%Y-%m")
        result = self._generate_cmd.execute(period)
        return GenerateBillsResponseDTO(**result)


# ── 新增: 逾期检查用例 ────────────────────────────────

class OverdueCheckUseCase:
    """
    逾期自动检查用例

    入口: POST /api/bills/schedule-overdue

    扫描所有 UNPAID 账单，将已过期的自动转为 OVERDUE。

    SQL:
        UPDATE fee_bills
           SET status = 'OVERDUE',
               updated_at = NOW()
         WHERE status = 'UNPAID'
           AND due_date < CURRENT_DATE;
    """

    def __init__(self, bill_repo: BillRepository):
        self._check_cmd = CheckOverdueCommand(bill_repo)

    def check(self) -> CheckOverdueResponseDTO:
        result = self._check_cmd.execute()
        return CheckOverdueResponseDTO(**result)