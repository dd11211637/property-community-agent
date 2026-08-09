"""
infrastructure/repositories.py     Repository 实现

实现 application/ports.py 中定义的仓储接口，
使用 SQLAlchemy ORM 操作数据库。

每个方法标注了等价 SQL 语句。
"""
from __future__ import annotations
from typing import Optional
from datetime import date as date_type, datetime as _dt
from sqlalchemy.orm import Session, joinedload

from .orm_models import (
    BillModel, BillingUserModel, PaymentModel, ReceiptModel,
    BuildingModel, RoomModel, BillingRuleModel, ConsultationModel,
)
from ..domain.entities import (
    Bill, User, Payment, Receipt, Building, Room, BillingRule, ConsultationTicket,
)
from ..domain.enums import (
    BillStatus, UserRole, PayMethod, PayStatus, BuildingType, RoomStatus,
    BuildingStatus, ConsultationStatus,
)
from ..application.ports import (
    BillRepository, UserRepository, PaymentRepository, ReceiptRepository,
    BuildingRepository, RoomRepository, UnitOfWork,
)

# ── 映射函数 ──────────────────────────────────────────

def _bill_from_model(m: BillModel) -> Bill:
    """ORM 模型 → 领域实体"""
    return Bill(
        bill_id=m.bill_id,
        user_id=m.user_id,
        room_id=m.room_id,
        bill_period=m.bill_period,
        property_fee=float(m.property_fee),
        utility_fee=float(m.utility_fee),
        parking_fee=float(m.parking_fee),
        late_fee=float(m.late_fee),
        total_amount=float(m.total_amount),
        due_date=m.due_date.isoformat() if m.due_date else "",
        status=BillStatus(m.status),
        payment_time=m.payment_time.strftime("%Y-%m-%d %H:%M:%S") if m.payment_time else None,
        receipt_no=m.receipt_no,
        community_id=m.community_id,
        house_id=m.house_id,
        version=m.version,
        fee_type=m.fee_type,
        source_time=m.source_time.isoformat() if m.source_time else None,
        rule_version=m.rule_version,
        rule_name=m.rule_name,
        user_name=m.user_ref.user_name if m.user_ref else "",
        building_name=m.room_ref.building.building_name if m.room_ref and m.room_ref.building else "",
        room_number=m.room_ref.room_number if m.room_ref else "",
    )


def _user_from_model(m: BillingUserModel) -> User:
    return User(
        user_id=m.user_id,
        user_name=m.user_name,
        role=UserRole(m.role),
        building_id=m.building_id,
        room_id=m.room_id,
        phone=m.phone or "",
        email=m.email or "",
    )


def _payment_from_model(m: PaymentModel) -> Payment:
    return Payment(
        payment_id=m.payment_id,
        bill_id=m.bill_id,
        user_id=m.user_id,
        pay_amount=float(m.pay_amount),
        pay_method=PayMethod(m.pay_method),
        pay_status=PayStatus(m.pay_status),
        transaction_id=m.transaction_id or "",
        receipt_no=m.receipt_no or "",
        paid_at=m.paid_at.strftime("%Y-%m-%d %H:%M:%S") if m.paid_at else "",
        user_name=m.user_ref.user_name if m.user_ref else "",
    )


def _receipt_from_model(m: ReceiptModel) -> Receipt:
    return Receipt(
        receipt_no=m.receipt_no,
        bill_id=m.bill_id,
        user_id=m.user_id,
        payment_id=m.payment_id,
        period=m.period,
        property_fee=float(m.property_fee),
        utility_fee=float(m.utility_fee),
        parking_fee=float(m.parking_fee),
        late_fee=float(m.late_fee),
        total_amount=float(m.total_amount),
        issue_time=m.issue_time.strftime("%Y-%m-%d %H:%M:%S") if m.issue_time else "",
        is_valid=m.is_valid,
        user_name=m.user_ref.user_name if m.user_ref else "",
        building_name=m.user_ref.building_ref.building_name if m.user_ref and m.user_ref.building_ref else "",
        room_number=m.user_ref.room_ref.room_number if m.user_ref and m.user_ref.room_ref else "",
        payment_time=m.payment_ref.paid_at.strftime("%Y-%m-%d %H:%M:%S") if m.payment_ref and m.payment_ref.paid_at else "",
    )


# ── BillRepository 实现 ──────────────────────────────

class SqlAlchemyBillRepository(BillRepository):
    """
    账单仓储实现

    核心 SQL:
        SELECT * FROM fee_bills WHERE user_id = :user_id ORDER BY bill_period DESC;
        SELECT * FROM fee_bills WHERE bill_id = :bill_id;
        UPDATE fee_bills SET status=:s, payment_time=:t, receipt_no=:rn WHERE bill_id=:id;
    """

    def __init__(self, db: Session):
        self._db = db

    def _query(self):
        return (
            self._db.query(BillModel)
            .options(
                joinedload(BillModel.user_ref),
                joinedload(BillModel.room_ref).joinedload(RoomModel.building),
            )
        )

    def find_by_user(self, user_id: str) -> list[Bill]:
        """
        SQL:
            SELECT f.*, u.user_name, r.room_number, b.building_name
              FROM fee_bills f
              JOIN sys_users u ON f.user_id = u.user_id
              JOIN community_rooms r ON f.room_id = r.room_id
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE f.user_id = :user_id
             ORDER BY f.bill_period DESC;
        """
        rows = (
            self._query()
            .filter(BillModel.user_id == user_id)
            .order_by(BillModel.bill_period.desc())
            .all()
        )
        return [_bill_from_model(r) for r in rows]

    def find_by_id(self, bill_id: str) -> Optional[Bill]:
        """
        SQL:
            SELECT * FROM fee_bills WHERE bill_id = :bill_id;
        """
        row = self._query().filter(BillModel.bill_id == bill_id).first()
        return _bill_from_model(row) if row else None

    def find_by_building(self, building_id: str) -> list[Bill]:
        """
        SQL:
            SELECT f.* FROM fee_bills f
            JOIN community_rooms r ON f.room_id = r.room_id
            WHERE r.building_id = :building_id
            ORDER BY f.bill_period DESC;
        """
        room_ids = (
            self._db.query(RoomModel.room_id)
            .filter(RoomModel.building_id == building_id)
            .subquery()
        )
        rows = (
            self._query()
            .filter(BillModel.room_id.in_(room_ids))
            .order_by(BillModel.bill_period.desc())
            .all()
        )
        return [_bill_from_model(r) for r in rows]

    def find_all(self) -> list[Bill]:
        """
        SQL:
            SELECT * FROM fee_bills ORDER BY bill_period DESC;
        """
        rows = self._query().order_by(BillModel.bill_period.desc()).all()
        return [_bill_from_model(r) for r in rows]

    def find_by_community_and_house(
        self,
        *,
        community_id: str,
        house_id: str | None = None,
        fee_type: str | None = None,
        period: str | None = None,
        status: str | None = None,
    ) -> list[Bill]:
        """PRD 6.3: 按社区(必选) + 房屋(可选) + 费用类型/账期/状态筛选。"""
        statement = self._query().filter(BillModel.community_id == community_id)
        if house_id is not None:
            statement = statement.filter(BillModel.house_id == house_id)
        if fee_type is not None:
            statement = statement.filter(BillModel.fee_type == fee_type)
        if period is not None:
            statement = statement.filter(BillModel.bill_period == period)
        if status is not None:
            statement = statement.filter(BillModel.status == status)
        rows = statement.order_by(BillModel.bill_period.desc()).all()
        return [_bill_from_model(r) for r in rows]

    def save(self, bill: Bill) -> Bill:
        """
        SQL:
            UPDATE fee_bills
               SET status = :status,
                   payment_time = :payment_time,
                   receipt_no = :receipt_no,
                   late_fee = :late_fee,
                   total_amount = :total_amount,
                   updated_at = NOW()
             WHERE bill_id = :bill_id;
        """
        row = self._db.query(BillModel).filter(BillModel.bill_id == bill.bill_id).first()
        if not row:
            raise ValueError(f"账单 {bill.bill_id} 不存在")
        row.status = bill.status.value if hasattr(bill.status, 'value') else bill.status
        row.payment_time = bill.payment_time
        row.receipt_no = bill.receipt_no
        row.late_fee = bill.late_fee
        row.total_amount = bill.total_amount
        self._db.flush()
        self._db.refresh(row)
        return bill

    def find_unpaid_or_overdue(self, user_id: str) -> list[Bill]:
        """
        SQL:
            SELECT * FROM fee_bills
            WHERE user_id = :user_id AND status IN ('UNPAID', 'OVERDUE');
        """
        rows = (
            self._query()
            .filter(
                BillModel.user_id == user_id,
                BillModel.status.in_(["UNPAID", "OVERDUE"]),
            )
            .all()
        )
        return [_bill_from_model(r) for r in rows]

    def find_unpaid_before_date(self, check_date: date_type) -> list[Bill]:
        """
        SQL:
            SELECT * FROM fee_bills
            WHERE status = 'UNPAID'
              AND due_date < :check_date;
        """
        rows = (
            self._query()
            .filter(
                BillModel.status == "UNPAID",
                BillModel.due_date < check_date,
            )
            .all()
        )
        return [_bill_from_model(r) for r in rows]

    def exists_by_user_and_period(self, user_id: str, period: str) -> bool:
        """
        SQL:
            SELECT COUNT(*) FROM fee_bills
            WHERE user_id = :user_id AND bill_period = :period;
        """
        return (
            self._db.query(BillModel)
            .filter(
                BillModel.user_id == user_id,
                BillModel.bill_period == period,
            )
            .first()
            is not None
        )

    def create(self, bill: Bill) -> Bill:
        """
        SQL:
            INSERT INTO fee_bills
                (bill_id, user_id, room_id, bill_period, property_fee, utility_fee,
                 parking_fee, late_fee, total_amount, due_date, status, created_at, updated_at)
            VALUES
                (:bill_id, :user_id, :room_id, :period, :property_fee, :utility_fee,
                 :parking_fee, 0, :total, :due_date, 'UNPAID', NOW(), NOW());
        """
        from datetime import datetime, date
        model = BillModel(
            bill_id=bill.bill_id,
            user_id=bill.user_id,
            room_id=bill.room_id,
            bill_period=bill.bill_period,
            property_fee=bill.property_fee,
            utility_fee=bill.utility_fee,
            parking_fee=bill.parking_fee,
            late_fee=bill.late_fee,
            total_amount=bill.total_amount,
            due_date=date.fromisoformat(bill.due_date),
            status=bill.status.value if hasattr(bill.status, 'value') else bill.status,
        )
        self._db.add(model)
        self._db.flush()
        return bill

    def bulk_update_status(self, bill_ids: list[str], new_status: str) -> int:
        """
        SQL:
            UPDATE fee_bills
               SET status = :new_status,
                   updated_at = NOW()
             WHERE bill_id IN (:bill_ids);
        """
        if not bill_ids:
            return 0
        count = (
            self._db.query(BillModel)
            .filter(BillModel.bill_id.in_(bill_ids))
            .update(
                {"status": new_status},
                synchronize_session="fetch",
            )
        )
        self._db.flush()
        return count


# ── UserRepository 实现 ──────────────────────────────

class SqlAlchemyUserRepository(UserRepository):
    """
    用户仓储实现

    SQL:
        SELECT * FROM sys_users WHERE user_id = :user_id;
    """

    def __init__(self, db: Session):
        self._db = db

    def find_by_id(self, user_id: str) -> Optional[User]:
        """
        SQL:
            SELECT u.*, b.building_name, r.room_number
              FROM sys_users u
              LEFT JOIN community_buildings b ON u.building_id = b.building_id
              LEFT JOIN community_rooms r ON u.room_id = r.room_id
             WHERE u.user_id = :user_id;
        """
        row = (
            self._db.query(BillingUserModel)
            .options(
                joinedload(BillingUserModel.building_ref),
                joinedload(BillingUserModel.room_ref),
            )
            .filter(BillingUserModel.user_id == user_id)
            .first()
        )
        if not row:
            return None
        user = _user_from_model(row)
        # 附加楼栋和房号信息
        user.building_name = row.building_ref.building_name if row.building_ref else ""
        user.room_number = row.room_ref.room_number if row.room_ref else ""
        return user

    def find_owner_by_room(self, room_id: str) -> Optional[User]:
        """
        SQL:
            SELECT * FROM sys_users
             WHERE room_id = :room_id
               AND role = 'owner'
               AND status = 'ACTIVE'
             LIMIT 1;
        """
        row = (
            self._db.query(BillingUserModel)
            .filter(
                BillingUserModel.room_id == room_id,
                BillingUserModel.role == "owner",
                BillingUserModel.status == "ACTIVE",
            )
            .first()
        )
        if not row:
            return None
        return _user_from_model(row)


# ── PaymentRepository 实现 ───────────────────────────

class SqlAlchemyPaymentRepository(PaymentRepository):
    """
    支付记录仓储实现

    SQL:
        SELECT payment_id FROM fee_payments ORDER BY payment_id DESC LIMIT 1;
        INSERT INTO fee_payments (...) VALUES (...);
        SELECT * FROM fee_payments WHERE user_id = :user_id ORDER BY paid_at DESC;
        SELECT * FROM fee_payments ORDER BY paid_at DESC;
    """

    def __init__(self, db: Session):
        self._db = db

    def get_last_payment_id(self) -> str | None:
        """
        SQL:
            SELECT payment_id FROM fee_payments ORDER BY payment_id DESC LIMIT 1;
        """
        row = (
            self._db.query(PaymentModel.payment_id)
            .order_by(PaymentModel.payment_id.desc())
            .first()
        )
        return row[0] if row else None

    def save(self, payment: Payment) -> Payment:
        """
        SQL:
            INSERT INTO fee_payments
                (payment_id, bill_id, user_id, pay_amount, pay_method, pay_status,
                 transaction_id, receipt_no, paid_at, created_at)
            VALUES
                (:payment_id, :bill_id, :user_id, :amount, :method, :status,
                 :txn_id, :receipt_no, :paid_at, NOW());
        """
        model = PaymentModel(
            payment_id=payment.payment_id,
            bill_id=payment.bill_id,
            user_id=payment.user_id,
            pay_amount=payment.pay_amount,
            pay_method=payment.pay_method.value if hasattr(payment.pay_method, 'value') else payment.pay_method,
            pay_status=payment.pay_status.value if hasattr(payment.pay_status, 'value') else payment.pay_status,
            transaction_id=payment.transaction_id,
            receipt_no=payment.receipt_no,
            paid_at=payment.paid_at,
        )
        self._db.add(model)
        self._db.flush()
        return payment

    def find_by_user(self, user_id: str) -> list[Payment]:
        """
        SQL:
            SELECT p.*, u.user_name
              FROM fee_payments p
              JOIN sys_users u ON p.user_id = u.user_id
             WHERE p.user_id = :user_id
             ORDER BY p.paid_at DESC;
        """
        rows = (
            self._db.query(PaymentModel)
            .options(joinedload(PaymentModel.user_ref))
            .filter(PaymentModel.user_id == user_id)
            .order_by(PaymentModel.paid_at.desc())
            .all()
        )
        return [_payment_from_model(r) for r in rows]

    def find_all(self) -> list[Payment]:
        """
        SQL:
            SELECT * FROM fee_payments ORDER BY paid_at DESC;
        """
        rows = (
            self._db.query(PaymentModel)
            .options(joinedload(PaymentModel.user_ref))
            .order_by(PaymentModel.paid_at.desc())
            .all()
        )
        return [_payment_from_model(r) for r in rows]

    def find_by_bill_id(self, bill_id: str) -> Optional[Payment]:
        """
        SQL:
            SELECT p.*, u.user_name
              FROM fee_payments p
              JOIN sys_users u ON p.user_id = u.user_id
             WHERE p.bill_id = :bill_id
               AND p.pay_status = 'SUCCESS'
             LIMIT 1;
        """
        row = (
            self._db.query(PaymentModel)
            .options(joinedload(PaymentModel.user_ref))
            .filter(
                PaymentModel.bill_id == bill_id,
                PaymentModel.pay_status == "SUCCESS",
            )
            .first()
        )
        return _payment_from_model(row) if row else None

    def update_status(self, payment_id: str, new_status: str) -> None:
        """
        SQL:
            UPDATE fee_payments
               SET pay_status = :new_status,
                   updated_at = NOW()
             WHERE payment_id = :payment_id;
        """
        (
            self._db.query(PaymentModel)
            .filter(PaymentModel.payment_id == payment_id)
            .update({"pay_status": new_status}, synchronize_session="fetch")
        )
        self._db.flush()


# ── ReceiptRepository 实现 ───────────────────────────

class SqlAlchemyReceiptRepository(ReceiptRepository):
    """
    票据仓储实现

    SQL:
        SELECT * FROM fee_receipts WHERE receipt_no = :receipt_no;
        INSERT INTO fee_receipts (...) VALUES (...);
    """

    def __init__(self, db: Session):
        self._db = db

    def find_by_no(self, receipt_no: str) -> Optional[Receipt]:
        """
        SQL:
            SELECT r.*, u.user_name, b.building_name, rm.room_number, p.paid_at
              FROM fee_receipts r
              JOIN sys_users u ON r.user_id = u.user_id
              LEFT JOIN community_buildings b ON u.building_id = b.building_id
              LEFT JOIN community_rooms rm ON u.room_id = rm.room_id
              JOIN fee_payments p ON r.payment_id = p.payment_id
             WHERE r.receipt_no = :receipt_no;
        """
        row = (
            self._db.query(ReceiptModel)
            .options(
                joinedload(ReceiptModel.user_ref).joinedload(BillingUserModel.building_ref),
                joinedload(ReceiptModel.user_ref).joinedload(BillingUserModel.room_ref),
                joinedload(ReceiptModel.payment_ref),
            )
            .filter(ReceiptModel.receipt_no == receipt_no)
            .first()
        )
        return _receipt_from_model(row) if row else None

    def save(self, receipt: Receipt) -> Receipt:
        """
        SQL:
            INSERT INTO fee_receipts
                (receipt_no, bill_id, user_id, payment_id, period,
                 property_fee, utility_fee, parking_fee, late_fee, total_amount,
                 issue_time, is_valid, created_at)
            VALUES
                (:receipt_no, :bill_id, :user_id, :payment_id, :period,
                 :property_fee, :utility_fee, :parking_fee, :late_fee, :total,
                 :issue_time, TRUE, NOW());
        """
        model = ReceiptModel(
            receipt_no=receipt.receipt_no,
            bill_id=receipt.bill_id,
            user_id=receipt.user_id,
            payment_id=receipt.payment_id,
            period=receipt.period,
            property_fee=receipt.property_fee,
            utility_fee=receipt.utility_fee,
            parking_fee=receipt.parking_fee,
            late_fee=receipt.late_fee,
            total_amount=receipt.total_amount,
            issue_time=receipt.issue_time,
        )
        self._db.add(model)
        self._db.flush()
        return receipt

    def invalidate_by_bill_id(self, bill_id: str) -> None:
        """
        SQL:
            UPDATE fee_receipts
               SET is_valid = FALSE
             WHERE bill_id = :bill_id;
        """
        (
            self._db.query(ReceiptModel)
            .filter(ReceiptModel.bill_id == bill_id)
            .update({"is_valid": False}, synchronize_session="fetch")
        )
        self._db.flush()


# ── BuildingRepository 实现 ───────────────────────────

def _building_from_model(m: BuildingModel) -> Building:
    """ORM 模型 → 领域实体"""
    return Building(
        building_id=m.building_id,
        building_name=m.building_name,
        building_type=BuildingType(m.building_type),
        total_floors=m.total_floors,
        total_units=m.total_units,
        address=m.address or "",
        status=BuildingStatus(m.status),
    )


class SqlAlchemyBuildingRepository(BuildingRepository):
    """
    楼栋仓储实现

    SQL:
        SELECT * FROM community_buildings WHERE building_id = :building_id;
        SELECT * FROM community_buildings ORDER BY building_name;
    """

    def __init__(self, db: Session):
        self._db = db

    def find_by_id(self, building_id: str) -> Optional[Building]:
        """
        SQL:
            SELECT * FROM community_buildings WHERE building_id = :building_id;
        """
        row = self._db.query(BuildingModel).filter(BuildingModel.building_id == building_id).first()
        return _building_from_model(row) if row else None

    def find_all(self) -> list[Building]:
        """
        SQL:
            SELECT * FROM community_buildings ORDER BY building_name;
        """
        rows = self._db.query(BuildingModel).order_by(BuildingModel.building_name).all()
        return [_building_from_model(r) for r in rows]


# ── RoomRepository 实现 ──────────────────────────────

def _room_from_model(m: RoomModel) -> Room:
    """ORM 模型 → 领域实体"""
    return Room(
        room_id=m.room_id,
        building_id=m.building_id,
        room_number=m.room_number,
        room_area=float(m.room_area),
        property_fee_rate=float(m.property_fee_rate),
        parking_spots=m.parking_spots,
        parking_fee_rate=float(m.parking_fee_rate),
        status=RoomStatus(m.status),
    )


class SqlAlchemyRoomRepository(RoomRepository):
    """
    房号仓储实现

    SQL:
        SELECT * FROM community_rooms WHERE room_id = :room_id;
        SELECT * FROM community_rooms WHERE building_id = :building_id;
    """

    def __init__(self, db: Session):
        self._db = db

    def find_by_id(self, room_id: str) -> Optional[Room]:
        """
        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE r.room_id = :room_id;
        """
        row = (
            self._db.query(RoomModel)
            .options(joinedload(RoomModel.building))
            .filter(RoomModel.room_id == room_id)
            .first()
        )
        return _room_from_model(row) if row else None

    def find_by_building(self, building_id: str) -> list[Room]:
        """
        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE r.building_id = :building_id
             ORDER BY r.room_number;
        """
        rows = (
            self._db.query(RoomModel)
            .options(joinedload(RoomModel.building))
            .filter(RoomModel.building_id == building_id)
            .order_by(RoomModel.room_number)
            .all()
        )
        return [_room_from_model(r) for r in rows]

    def find_all(self) -> list[Room]:
        """
        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             ORDER BY b.building_name, r.room_number;
        """
        rows = (
            self._db.query(RoomModel)
            .options(joinedload(RoomModel.building))
            .order_by(RoomModel.building_id, RoomModel.room_number)
            .all()
        )
        return [_room_from_model(r) for r in rows]

    def find_active_rooms(self) -> list[Room]:
        """
        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE r.status = 'OCCUPIED';
        """
        rows = (
            self._db.query(RoomModel)
            .options(joinedload(RoomModel.building))
            .filter(RoomModel.status == "OCCUPIED")
            .all()
        )
        return [_room_from_model(r) for r in rows]


# ── UnitOfWork 实现 ──────────────────────────────────

class SqlAlchemyUnitOfWork(UnitOfWork):
    """
    工作单元实现

    管理数据库事务边界，确保多个 Repository 操作在同一事务中执行。

    SQL:
        BEGIN;
        -- 多个 Repository 操作（flush）
        COMMIT;  -- 或 ROLLBACK;

    使用示例:
        uow = SqlAlchemyUnitOfWork(db)
        try:
            bill_repo.save(bill)      # flush
            payment_repo.save(pay)    # flush
            receipt_repo.save(rec)    # flush
            uow.commit()              # 一起提交
        except Exception:
            uow.rollback()
            raise
    """

    def __init__(self, db: Session):
        self._db = db

    def commit(self) -> None:
        """
        提交事务

        SQL:
            COMMIT;
        """
        self._db.commit()

    def rollback(self) -> None:
        """
        回滚事务

        SQL:
            ROLLBACK;
        """
        self._db.rollback()


# ── BillingRuleRepository 实现 ──────────────────────────

class SqlAlchemyBillingRuleRepository:
    """计费规则仓储实现（PRD 6.3）。"""

    def __init__(self, db: Session):
        self._db = db

    def find_effective(
        self, community_id: str, fee_type: str, as_of: Optional[str] = None
    ) -> Optional[BillingRule]:
        from datetime import datetime as _dt

        now = _dt.fromisoformat(as_of) if as_of else _dt.now()
        rows = (
            self._db.query(BillingRuleModel)
            .filter(
                BillingRuleModel.community_id == community_id,
                BillingRuleModel.fee_type == fee_type,
            )
            .all()
        )
        for m in rows:
            valid_from = m.valid_from
            valid_until = m.valid_until
            if valid_from and now < valid_from:
                continue
            if valid_until and now > valid_until:
                continue
            return BillingRule(
                id=m.id,
                community_id=m.community_id,
                fee_type=m.fee_type,
                version=m.version,
                name=m.name,
                parameters=dict(m.parameters) if m.parameters else None,
                valid_from=m.valid_from.isoformat() if m.valid_from else None,
                valid_until=m.valid_until.isoformat() if m.valid_until else None,
            )
        return None

    def save(self, rule: BillingRule) -> BillingRule:
        model = BillingRuleModel(
            id=rule.id,
            community_id=rule.community_id,
            fee_type=rule.fee_type,
            version=rule.version,
            name=rule.name,
            parameters=rule.parameters,
            valid_from=(
                _dt.fromisoformat(rule.valid_from) if rule.valid_from else _dt.now()
            ),
            valid_until=(
                _dt.fromisoformat(rule.valid_until) if rule.valid_until else None
            ),
        )
        self._db.add(model)
        self._db.flush()
        return rule


# ── ConsultationRepository 实现 ────────────────────────

class SqlAlchemyConsultationRepository:
    """财务咨询单仓储实现（PRD 6.3）。"""

    def __init__(self, db: Session):
        self._db = db

    def add(self, ticket: ConsultationTicket) -> ConsultationTicket:
        model = ConsultationModel(
            id=ticket.id,
            community_id=ticket.community_id,
            house_id=ticket.house_id,
            actor_id=ticket.actor_id,
            bill_id=ticket.bill_id,
            subject=ticket.subject,
            description=ticket.description,
            status=ticket.status.value,
            answer=ticket.answer,
            handler_id=ticket.handler_id,
            version=ticket.version,
        )
        self._db.add(model)
        self._db.flush()
        return ticket

    def get(self, consultation_id: str) -> Optional[ConsultationTicket]:
        m = self._db.query(ConsultationModel).filter(
            ConsultationModel.id == consultation_id
        ).first()
        return _consultation_from_model(m) if m else None

    def list_by_actor(self, actor_id: str, community_id: str) -> list[ConsultationTicket]:
        rows = (
            self._db.query(ConsultationModel)
            .filter(
                ConsultationModel.actor_id == actor_id,
                ConsultationModel.community_id == community_id,
            )
            .order_by(ConsultationModel.created_at.desc())
            .all()
        )
        return [_consultation_from_model(r) for r in rows]

    def update(self, ticket: ConsultationTicket) -> ConsultationTicket:
        m = self._db.query(ConsultationModel).filter(
            ConsultationModel.id == ticket.id
        ).first()
        if not m:
            raise ValueError(f"咨询单 {ticket.id} 不存在")
        m.status = ticket.status.value
        m.answer = ticket.answer
        m.handler_id = ticket.handler_id
        m.house_id = ticket.house_id
        m.bill_id = ticket.bill_id
        m.version = ticket.version
        m.updated_at = _dt.now()
        self._db.flush()
        return ticket


def _consultation_from_model(m: ConsultationModel) -> ConsultationTicket:
    return ConsultationTicket(
        id=m.id,
        community_id=m.community_id,
        actor_id=m.actor_id,
        subject=m.subject,
        description=m.description,
        house_id=m.house_id,
        bill_id=m.bill_id,
        status=ConsultationStatus(m.status),
        answer=m.answer,
        handler_id=m.handler_id,
        version=m.version,
        created_at=m.created_at.isoformat() if m.created_at else None,
        updated_at=m.updated_at.isoformat() if m.updated_at else None,
    )