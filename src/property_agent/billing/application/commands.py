"""
application/commands.py     命令（Command）

写操作，修改系统状态。
每个命令方法标注了对应的等价 SQL 语句。
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from .ports import BillRepository, PaymentRepository, ReceiptRepository, PaymentGateway, UnitOfWork, UserRepository, RoomRepository
from ..domain.entities import Bill, Payment, Receipt
from ..domain.enums import BillStatus, PayMethod, PayStatus
from ..domain.state_machine import transition_to, can_transition
from ..domain.business_rules import (
    validate_payable, validate_cancellable, validate_refundable,
    generate_receipt_no, generate_payment_id, generate_transaction_id,
    calculate_late_fee, bills_to_csv,
)


class PayBillCommand:
    """
    缴费命令

    SQL:
        -- 前置校验: 查询账单状态
        SELECT bill_id, status, total_amount
          FROM fee_bills
         WHERE bill_id = :bill_id;

        -- 事务开始
        BEGIN;

        -- 更新账单状态
        UPDATE fee_bills
           SET status = 'PAID',
               payment_time = NOW(),
               receipt_no = :receipt_no,
               updated_at = NOW()
         WHERE bill_id = :bill_id;

        -- 创建支付记录
        INSERT INTO fee_payments
            (payment_id, bill_id, user_id, pay_amount, pay_method, pay_status,
             transaction_id, receipt_no, paid_at, created_at)
        VALUES
            (:payment_id, :bill_id, :user_id, :amount, :method, 'SUCCESS',
             :txn_id, :receipt_no, NOW(), NOW());

        -- 创建电子票据
        INSERT INTO fee_receipts
            (receipt_no, bill_id, user_id, payment_id, period,
             property_fee, utility_fee, parking_fee, late_fee, total_amount,
             issue_time, is_valid, created_at)
        VALUES
            (:receipt_no, :bill_id, :user_id, :payment_id, :period,
             :property_fee, :utility_fee, :parking_fee, :late_fee, :total,
             NOW(), TRUE, NOW());

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
        self._bill_repo = bill_repo
        self._payment_repo = payment_repo
        self._receipt_repo = receipt_repo
        self._payment_gateway = payment_gateway
        self._uow = uow

    def execute(self, bill_id: str, user_id: str) -> dict:
        """
        执行缴费流程。

        前置 SQL:
            SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            -- 应用层校验: validate_payable(bill)

        事务 SQL:
            BEGIN;
            UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
            INSERT INTO fee_payments (...) VALUES (...);
            INSERT INTO fee_receipts (...) VALUES (...);
            COMMIT;
        """
        # 1. 查询账单（前置校验 SELECT）
        bill = self._bill_repo.find_by_id(bill_id)
        if not bill:
            raise ValueError(f"账单 {bill_id} 不存在")

        # 2. 校验可支付
        ok, reason = validate_payable(bill)
        if not ok:
            raise ValueError(reason)

        try:
            # 3. 通过支付网关处理支付（生成 Payment 对象）
            payment = self._payment_gateway.process_payment(bill, user_id)
            receipt_no = payment.receipt_no

            # 4. 状态转换
            now = datetime.now()
            transition_to(bill, BillStatus.PAID,
                          payment_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                          receipt_no=receipt_no)

            # 5. 保存账单（flush）
            self._bill_repo.save(bill)

            # 6. 保存支付记录（flush）
            self._payment_repo.save(payment)

            # 7. 创建并保存电子票据（flush）
            receipt = Receipt(
                receipt_no=receipt_no,
                bill_id=bill_id,
                user_id=user_id,
                payment_id=payment.payment_id,
                period=bill.bill_period,
                property_fee=bill.property_fee,
                utility_fee=bill.utility_fee,
                parking_fee=bill.parking_fee,
                late_fee=bill.late_fee,
                total_amount=bill.total_amount,
                issue_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            )
            self._receipt_repo.save(receipt)

            # 8. 提交事务（COMMIT）
            self._uow.commit()

            return {
                "success": True,
                "message": "缴费成功！您的电子票据已生成。",
                "bill_id": bill_id,
                "receipt_no": receipt_no,
                "paid_amount": bill.total_amount,
                "payment_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception:
            self._uow.rollback()
            raise


class BatchPayCommand:
    """
    批量缴费命令

    SQL:
        -- 对每笔账单循环执行:
        SELECT * FROM fee_bills WHERE bill_id = :bill_id;  -- 前置校验
        -- 每笔缴费在独立事务中执行:
        BEGIN;
        UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
        INSERT INTO fee_payments (...) VALUES (...);
        INSERT INTO fee_receipts (...) VALUES (...);
        COMMIT;
        -- 汇总结果:
        SELECT COUNT(*) FROM fee_payments WHERE paid_at >= :batch_start;
    """

    def __init__(self, pay_command: PayBillCommand):
        self._pay_command = pay_command

    def execute(self, bill_ids: list[str], user_id: str) -> dict:
        results = []
        success_count = 0
        failed_count = 0
        total_paid = 0.0

        for bill_id in bill_ids:
            try:
                result = self._pay_command.execute(bill_id, user_id)
                results.append(result)
                success_count += 1
                total_paid += result["paid_amount"]
            except ValueError as e:
                results.append({
                    "success": False,
                    "message": str(e),
                    "bill_id": bill_id,
                    "receipt_no": "",
                    "paid_amount": 0.0,
                    "payment_time": "",
                })
                failed_count += 1

        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "results": results,
            "total_paid": round(total_paid, 2),
        }


class UpdateLateFeeCommand:
    """
    更新滞纳金命令

    SQL:
        -- 前置查询
        SELECT bill_id, status, property_fee, utility_fee, parking_fee,
               late_fee, total_amount, due_date
          FROM fee_bills
         WHERE bill_id = :bill_id;

        -- 更新滞纳金
        UPDATE fee_bills
           SET late_fee = :late_fee,
               total_amount = property_fee + utility_fee + parking_fee + :late_fee,
               updated_at = NOW()
         WHERE bill_id = :bill_id;
    """

    def __init__(self, bill_repo: BillRepository):
        self._bill_repo = bill_repo

    def execute(self, bill_id: str) -> float:
        """
        重新计算并更新滞纳金，返回新滞纳金金额。

        前置 SQL:
            SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            -- 应用层计算: calculate_late_fee(bill)

        更新 SQL:
            UPDATE fee_bills SET late_fee=:f, total_amount=:t, updated_at=NOW() WHERE bill_id=:id;
        """
        bill = self._bill_repo.find_by_id(bill_id)
        if not bill:
            raise ValueError(f"账单 {bill_id} 不存在")

        new_late_fee = calculate_late_fee(bill)
        bill.late_fee = new_late_fee
        bill.total_amount = (
            bill.property_fee
            + bill.utility_fee
            + bill.parking_fee
            + new_late_fee
        )
        self._bill_repo.save(bill)
        return new_late_fee


# ── 新增: 取消账单命令 ───────────────────────────────

class CancelBillCommand:
    """
    取消账单命令（管理员专用）

    SQL:
        -- 前置校验
        SELECT status FROM fee_bills WHERE bill_id = :bill_id;
        -- 应用层判断: validate_cancellable(bill)

        -- 更新状态
        UPDATE fee_bills
           SET status = 'CANCELLED',
               updated_at = NOW()
         WHERE bill_id = :bill_id;
    """

    def __init__(self, bill_repo: BillRepository):
        self._bill_repo = bill_repo

    def execute(self, bill_id: str, reason: str = "管理员手动作废") -> dict:
        """
        执行取消账单。

        前置 SQL:
            SELECT status FROM fee_bills WHERE bill_id = :bill_id;
            -- 应用层校验: validate_cancellable(bill)

        更新 SQL:
            UPDATE fee_bills SET status='CANCELLED', updated_at=NOW() WHERE bill_id=:bill_id;
        """
        bill = self._bill_repo.find_by_id(bill_id)
        if not bill:
            raise ValueError(f"账单 {bill_id} 不存在")

        ok, reason_text = validate_cancellable(bill)
        if not ok:
            raise ValueError(reason_text)

        previous_status = bill.status.value if hasattr(bill.status, 'value') else bill.status

        transition_to(bill, BillStatus.CANCELLED)
        self._bill_repo.save(bill)

        return {
            "success": True,
            "message": f"账单已作废。原因: {reason}",
            "bill_id": bill_id,
            "previous_status": previous_status,
        }


# ── 新增: 退款命令 ───────────────────────────────────

class RefundBillCommand:
    """
    退款命令（管理员专用）

    将已缴费账单退回到未缴费状态，支付记录标记为 REFUNDED。

    SQL:
        -- 前置校验
        SELECT f.status, p.pay_status
          FROM fee_bills f
          JOIN fee_payments p ON f.bill_id = p.bill_id
         WHERE f.bill_id = :bill_id;

        -- 事务开始
        BEGIN;

        -- 更新账单状态为 UNPAID
        UPDATE fee_bills
           SET status = 'UNPAID',
               payment_time = NULL,
               receipt_no = NULL,
               updated_at = NOW()
         WHERE bill_id = :bill_id;

        -- 更新支付记录为 REFUNDED
        UPDATE fee_payments
           SET pay_status = 'REFUNDED',
               updated_at = NOW()
         WHERE bill_id = :bill_id
           AND pay_status = 'SUCCESS';

        -- 作废电子票据
        UPDATE fee_receipts
           SET is_valid = FALSE
         WHERE bill_id = :bill_id;

        COMMIT;
    """

    def __init__(
        self,
        bill_repo: BillRepository,
        payment_repo: PaymentRepository,
        receipt_repo: ReceiptRepository,
        uow: UnitOfWork,
    ):
        self._bill_repo = bill_repo
        self._payment_repo = payment_repo
        self._receipt_repo = receipt_repo
        self._uow = uow

    def execute(self, bill_id: str, reason: str = "管理员退款") -> dict:
        """
        执行退款流程。

        前置 SQL:
            SELECT f.status, p.pay_status
              FROM fee_bills f
              JOIN fee_payments p ON f.bill_id = p.bill_id
             WHERE f.bill_id = :bill_id;

        事务 SQL:
            BEGIN;
            UPDATE fee_bills SET status='UNPAID', payment_time=NULL, receipt_no=NULL WHERE bill_id=:id;
            UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:id;
            UPDATE fee_receipts SET is_valid=FALSE WHERE bill_id=:id;
            COMMIT;
        """
        bill = self._bill_repo.find_by_id(bill_id)
        if not bill:
            raise ValueError(f"账单 {bill_id} 不存在")

        ok, reason_text = validate_refundable(bill)
        if not ok:
            raise ValueError(reason_text)

        try:
            now = datetime.now()

            # 1. 账单状态回退到 UNPAID
            bill.status = BillStatus.UNPAID
            bill.payment_time = None
            bill.receipt_no = None
            self._bill_repo.save(bill)

            # 2. 更新支付记录状态为 REFUNDED
            payment = self._payment_repo.find_by_bill_id(bill_id)
            if payment:
                self._payment_repo.update_status(payment.payment_id, "REFUNDED")

            # 3. 作废电子票据
            self._receipt_repo.invalidate_by_bill_id(bill_id)

            self._uow.commit()

            return {
                "success": True,
                "message": f"退款成功。原因: {reason}",
                "bill_id": bill_id,
                "refund_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception:
            self._uow.rollback()
            raise


# ── 新增: 账单生成命令 ───────────────────────────────

class GenerateBillsCommand:
    """
    批量生成账单命令

    为指定账期生成所有活跃房号的账单。

    SQL:
        -- 查询所有活跃房号
        SELECT r.room_id, r.room_area, r.property_fee_rate, r.parking_spots,
               r.parking_fee_rate, r.building_id, b.building_name, r.room_number
          FROM community_rooms r
          JOIN community_buildings b ON r.building_id = b.building_id
         WHERE r.status = 'OCCUPIED';

        -- 查询房号对应的业主
        SELECT user_id FROM sys_users
         WHERE room_id = :room_id AND role = 'owner' AND status = 'ACTIVE'
         LIMIT 1;

        -- 检查是否已存在同账期账单
        SELECT COUNT(*) FROM fee_bills
         WHERE user_id = :user_id AND bill_period = :period;

        -- 插入新账单
        INSERT INTO fee_bills
            (bill_id, user_id, room_id, bill_period, property_fee, utility_fee,
             parking_fee, late_fee, total_amount, due_date, status, created_at, updated_at)
        VALUES
            (:bill_id, :user_id, :room_id, :period, :property_fee, :utility_fee,
             :parking_fee, 0, :total, :due_date, 'UNPAID', NOW(), NOW());
    """

    def __init__(
        self,
        bill_repo: BillRepository,
        user_repo: UserRepository,
        room_repo: RoomRepository,
    ):
        self._bill_repo = bill_repo
        self._user_repo = user_repo
        self._room_repo = room_repo

    def execute(self, period: str) -> dict:
        """
        执行账单批量生成。

        返回: {generated_count, skipped_count, period}
        """
        from ..domain.business_rules import (
            generate_bill_id, calculate_due_date, calculate_bill_fees,
        )

        # 1. 查询所有活跃房号
        rooms = self._room_repo.find_active_rooms()

        generated = 0
        skipped = 0
        due_date = calculate_due_date(period)

        for room in rooms:
            # 2. 查询房号对应的业主
            user = self._user_repo.find_owner_by_room(room.room_id)
            if not user:
                skipped += 1
                continue

            # 3. 检查是否已存在同账期账单
            if self._bill_repo.exists_by_user_and_period(user.user_id, period):
                skipped += 1
                continue

            # 4. 计算费用
            fees = calculate_bill_fees({
                "room_area": room.room_area,
                "property_fee_rate": room.property_fee_rate,
                "parking_spots": room.parking_spots,
                "parking_fee_rate": room.parking_fee_rate,
            })

            # 5. 创建账单
            bill_id = generate_bill_id(room.room_id, period)
            from ..domain.entities import Bill
            from ..domain.enums import BillStatus
            new_bill = Bill(
                bill_id=bill_id,
                user_id=user.user_id,
                room_id=room.room_id,
                bill_period=period,
                property_fee=fees["property_fee"],
                utility_fee=fees["utility_fee"],
                parking_fee=fees["parking_fee"],
                late_fee=0,
                total_amount=fees["total_amount"],
                due_date=due_date,
                status=BillStatus.UNPAID,
            )
            self._bill_repo.create(new_bill)
            generated += 1

        return {
            "generated_count": generated,
            "skipped_count": skipped,
            "period": period,
            "due_date": due_date,
        }


# ── 新增: 逾期批量检查命令 ───────────────────────────

class CheckOverdueCommand:
    """
    逾期批量检查命令

    扫描所有 UNPAID 账单，将已过期的自动转为 OVERDUE。

    SQL:
        UPDATE fee_bills
           SET status = 'OVERDUE',
               updated_at = NOW()
         WHERE status = 'UNPAID'
           AND due_date < CURRENT_DATE;
    """

    def __init__(self, bill_repo: BillRepository):
        self._bill_repo = bill_repo

    def execute(self) -> dict:
        """
        执行逾期批量检查。

        返回: {updated_count, total_checked, check_time}
        """
        from datetime import date

        # 查询所有 UNPAID 且已过期的账单
        overdue_bills = self._bill_repo.find_unpaid_before_date(date.today())

        if overdue_bills:
            bill_ids = [b.bill_id for b in overdue_bills]
            self._bill_repo.bulk_update_status(bill_ids, "OVERDUE")

        return {
            "updated_count": len(overdue_bills),
            "total_checked": len(overdue_bills),
            "check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


# ── 新增: 账单导出命令 ───────────────────────────────

class ExportBillsCommand:
    """
    账单导出命令

    将账单数据导出为 CSV 格式。按角色区分数据范围：
    - owner: 查询本人账单
    - staff: 查询所负责楼栋的账单
    - admin: 查询全社区账单

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

    def __init__(self, bill_repo: BillRepository):
        self._bill_repo = bill_repo

    def execute(self, user_id: str = "", role: str = "owner", building_id: str = "") -> dict:
        if role == "owner":
            # SQL: SELECT * FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;
            bills = self._bill_repo.find_by_user(user_id)
        elif role == "staff":
            # SQL: SELECT f.* FROM fee_bills f JOIN community_rooms r ON f.room_id = r.room_id
            #      WHERE r.building_id = :building_id ORDER BY f.bill_period DESC;
            bills = self._bill_repo.find_by_building(building_id) if building_id else []
        elif role == "admin":
            # SQL: SELECT * FROM fee_bills ORDER BY bill_period DESC;
            bills = self._bill_repo.find_all()
        else:
            bills = self._bill_repo.find_by_user(user_id)

        csv_content = bills_to_csv(bills)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"bills_export_{role}_{timestamp}.csv"

        return {
            "csv_content": csv_content,
            "filename": filename,
            "total_count": len(bills),
        }