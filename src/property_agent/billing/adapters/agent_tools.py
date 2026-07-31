"""
adapters/agent_tools.py     AI Agent 工具封装

将 billing 模块的能力封装为 AI Agent 可调用的工具函数，
供 agent/ 层的智能体编排使用。

调用链:
    Agent 调度器
      → BillingAgentTools.query_bills(user_id, role)
        → BillQueryUseCase.list_bills(user_id, role)
          → GetBillsByRole.execute(user_id, role)
            → BillRepository.find_by_user() / find_all()
              → SQL: SELECT * FROM fee_bills WHERE ...

    Agent 调度器
      → BillingAgentTools.interpret_bill(bill_id, user_id)
        → InterpretationUseCase.interpret(bill_id, user_id)
          → GetBillById → BillRepository.find_by_id()
          → HttpLLMClient.interpret_bill()
          → 返回 (解读文本, 提醒层级, 提醒文案)

    Agent 调度器
      → BillingAgentTools.pay_bill(bill_id, user_id)
        → PaymentUseCase.pay_single(bill_id, user_id)
          → PayBillCommand.execute(bill_id, user_id)
            → SQL: BEGIN; UPDATE fee_bills ...; INSERT fee_payments ...; INSERT fee_receipts ...; COMMIT;
"""
from __future__ import annotations
from typing import Optional
from sqlalchemy.orm import Session

from ..application.use_cases import (
    BillQueryUseCase, PaymentUseCase, InterpretationUseCase,
    ReceiptUseCase, PaymentHistoryUseCase, ExportUseCase, CancelBillUseCase,
    RefundUseCase, BillGenerationUseCase, OverdueCheckUseCase,
)
from ..infrastructure.repositories import (
    SqlAlchemyBillRepository,
    SqlAlchemyUserRepository,
    SqlAlchemyPaymentRepository,
    SqlAlchemyReceiptRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyRoomRepository,
)
from ..infrastructure.llm_client import HttpLLMClient
from ..infrastructure.payment_gateway import MockPaymentGateway


class BillingAgentTools:
    """
    AI Agent 可调用的费用管理工具集。

    提供给 agent/ 层的智能体编排使用，例如:
        - 当用户说"帮我查下这个月物业费多少钱" → 调用 query_bills()
        - 当用户说"帮我交一下7月的物业费" → 调用 pay_bill()
        - 当用户说"这账单怎么这么多钱" → 调用 interpret_bill()

    每个工具方法内部自动完成:
        1. 数据库查询 (SQL)
        2. 业务规则校验 (Domain)
        3. 结果返回 (Dict)
    """

    def __init__(self, db: Session):
        """
        初始化工具集，注入所有依赖。

        SQL 连接: 通过 Session 参数注入，由调用方管理生命周期。
        """
        self._db = db
        self._query_uc = BillQueryUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
            user_repo=SqlAlchemyUserRepository(db),
        )
        self._payment_uc = PaymentUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
            payment_repo=SqlAlchemyPaymentRepository(db),
            receipt_repo=SqlAlchemyReceiptRepository(db),
            payment_gateway=MockPaymentGateway(),
            uow=SqlAlchemyUnitOfWork(db),
        )
        self._interpret_uc = InterpretationUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
            user_repo=SqlAlchemyUserRepository(db),
            llm_client=HttpLLMClient(),
        )
        self._receipt_uc = ReceiptUseCase(
            receipt_repo=SqlAlchemyReceiptRepository(db),
        )
        self._history_uc = PaymentHistoryUseCase(
            payment_repo=SqlAlchemyPaymentRepository(db),
        )
        self._export_uc = ExportUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
        )
        self._cancel_uc = CancelBillUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
        )
        self._refund_uc = RefundUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
            payment_repo=SqlAlchemyPaymentRepository(db),
            receipt_repo=SqlAlchemyReceiptRepository(db),
            uow=SqlAlchemyUnitOfWork(db),
        )
        self._generation_uc = BillGenerationUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
            user_repo=SqlAlchemyUserRepository(db),
            room_repo=SqlAlchemyRoomRepository(db),
        )
        self._overdue_uc = OverdueCheckUseCase(
            bill_repo=SqlAlchemyBillRepository(db),
        )

    def query_bills(self, user_id: str, role: str = "owner") -> dict:
        """
        查询账单

        SQL:
            SELECT * FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;
        """
        result = self._query_uc.list_bills(user_id, role)
        from dataclasses import asdict
        return asdict(result)

    async def interpret_bill(self, bill_id: str, user_id: str) -> dict:
        """
        AI 解读账单

        SQL:
            SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            SELECT user_name FROM sys_users WHERE user_id = :user_id;
        """
        result = await self._interpret_uc.interpret(bill_id, user_id)
        from dataclasses import asdict
        return asdict(result)

    def pay_bill(self, bill_id: str, user_id: str) -> dict:
        """
        缴费

        SQL:
            BEGIN;
            UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
            INSERT INTO fee_payments (...) VALUES (...);
            INSERT INTO fee_receipts (...) VALUES (...);
            COMMIT;
        """
        result = self._payment_uc.pay_single(bill_id, user_id)
        from dataclasses import asdict
        return asdict(result)

    def get_user(self, user_id: str) -> Optional[dict]:
        """
        查询用户

        SQL:
            SELECT * FROM sys_users WHERE user_id = :user_id;
        """
        return self._query_uc.get_user(user_id)

    def pay_batch(self, bill_ids: list[str], user_id: str) -> dict:
        """
        批量缴费

        SQL:
            -- 对每笔账单循环执行:
            BEGIN;
            UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
            INSERT INTO fee_payments (...) VALUES (...);
            INSERT INTO fee_receipts (...) VALUES (...);
            COMMIT;
        """
        result = self._payment_uc.pay_batch(bill_ids, user_id)
        from dataclasses import asdict
        return asdict(result)

    def get_receipt(self, receipt_no: str) -> Optional[dict]:
        """
        查询电子票据

        SQL:
            SELECT * FROM fee_receipts WHERE receipt_no = :receipt_no;
        """
        result = self._receipt_uc.get_receipt(receipt_no)
        if not result:
            return None
        from dataclasses import asdict
        return asdict(result)

    def get_payment_history(self, user_id: str, role: str = "owner") -> dict:
        """
        查询支付历史

        SQL (owner):
            SELECT p.*, u.user_name
              FROM fee_payments p
              JOIN sys_users u ON p.user_id = u.user_id
             WHERE p.user_id = :user_id
             ORDER BY p.paid_at DESC;

        SQL (admin):
            SELECT * FROM fee_payments ORDER BY paid_at DESC;
        """
        if role == "admin":
            result = self._history_uc.get_all_history()
        else:
            result = self._history_uc.get_user_history(user_id)
        from dataclasses import asdict
        return asdict(result)

    def export_bills_csv(self, user_id: str, role: str = "owner") -> dict:
        """
        导出账单为 CSV

        SQL:
            SELECT f.*, u.user_name, b.building_name, r.room_number
              FROM fee_bills f
              JOIN sys_users u ON f.user_id = u.user_id
              JOIN community_rooms r ON f.room_id = r.room_id
              JOIN community_buildings b ON r.building_id = b.building_id
             ORDER BY f.bill_period DESC;
        """
        result = self._export_uc.export_csv(user_id, role)
        from dataclasses import asdict
        return asdict(result)

    def cancel_bill(self, bill_id: str, reason: str = "管理员手动作废") -> dict:
        """
        取消账单（管理员专用）

        SQL:
            UPDATE fee_bills
               SET status = 'CANCELLED',
                   updated_at = NOW()
             WHERE bill_id = :bill_id;
        """
        try:
            result = self._cancel_uc.cancel(bill_id, reason)
            from dataclasses import asdict
            return asdict(result)
        except ValueError as e:
            return {"success": False, "message": str(e), "bill_id": bill_id}

    def refund_bill(self, bill_id: str, reason: str = "管理员退款") -> dict:
        """
        退款（管理员专用）

        SQL:
            BEGIN;
            UPDATE fee_bills SET status='UNPAID', payment_time=NULL, receipt_no=NULL WHERE bill_id=:id;
            UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:id;
            UPDATE fee_receipts SET is_valid=FALSE WHERE bill_id=:id;
            COMMIT;
        """
        try:
            result = self._refund_uc.refund(bill_id, reason)
            from dataclasses import asdict
            return asdict(result)
        except ValueError as e:
            return {"success": False, "message": str(e), "bill_id": bill_id}

    def generate_bills(self, period: str = "") -> dict:
        """
        批量生成账单（管理员专用）

        SQL:
            INSERT INTO fee_bills (...) VALUES (...)  -- 每间房一条
        """
        result = self._generation_uc.generate(period)
        from dataclasses import asdict
        return asdict(result)

    def check_overdue(self) -> dict:
        """
        逾期自动检查

        SQL:
            UPDATE fee_bills SET status='OVERDUE' WHERE status='UNPAID' AND due_date < CURRENT_DATE;
        """
        result = self._overdue_uc.check()
        from dataclasses import asdict
        return asdict(result)