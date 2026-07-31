"""
domain/entities.py     实体

领域实体定义，每个实体对应数据库中的一张表。
实体是领域层的核心，包含业务属性和行为方法。

────────────────────────────────────────────────────────
6 个实体:
────────────────────────────────────────────────────────
  Building    楼栋实体    → community_buildings   (PK: building_id)
  Room        房号实体    → community_rooms       (PK: room_id, FK: building_id)
  User        用户实体    → sys_users             (PK: user_id, FK: building_id, room_id)
  Bill        账单实体    → fee_bills             (PK: bill_id, FK: user_id, room_id)
  Payment     缴费记录    → fee_payments          (PK: payment_id, FK: bill_id, user_id)
  Receipt     电子票据    → fee_receipts          (PK: receipt_no, FK: bill_id, user_id, payment_id)

────────────────────────────────────────────────────────
调用链（以缴费为例）:
────────────────────────────────────────────────────────
  PaymentUseCase.pay_single()
    → PayBillCommand.execute()
      → BillRepository.find_by_id(bill_id)          -- 查询 Bill 实体
        → SQL: SELECT * FROM fee_bills WHERE bill_id = :bill_id;
      → PaymentGateway.process_payment(bill, user_id) -- 生成 Payment 实体
        → SQL: INSERT INTO fee_payments (...) VALUES (...);
      → transition_to(bill, PAID)                    -- 状态转换
      → Receipt(...)                                  -- 创建 Receipt 实体
        → SQL: INSERT INTO fee_receipts (...) VALUES (...);
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from .enums import BillStatus, UserRole, PayMethod, PayStatus, BuildingType, RoomStatus, UserStatus, BuildingStatus
from .value_objects import Money, FeeDetail, BillPeriod, Address


# ═══════════════════════════════════════════════════════════════
# Building · 楼栋实体
# ═══════════════════════════════════════════════════════════════

@dataclass
class Building:
    """
    楼栋实体

    对应表: community_buildings

    SQL:
        CREATE TABLE community_buildings (
            building_id     VARCHAR(32)  PRIMARY KEY,
            building_name   VARCHAR(64)  NOT NULL,
            building_type   VARCHAR(16)  NOT NULL DEFAULT 'RESIDENTIAL'
                CHECK (building_type IN ('RESIDENTIAL', 'COMMERCIAL', 'OFFICE')),
            total_floors    INTEGER      NOT NULL DEFAULT 1
                CHECK (total_floors >= 1),
            total_units     INTEGER      NOT NULL DEFAULT 0
                CHECK (total_units >= 0),
            address         VARCHAR(256),
            status          VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE')),
            created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        SELECT * FROM community_buildings WHERE building_id = :building_id;
    """
    building_id: str
    building_name: str
    building_type: BuildingType = BuildingType.RESIDENTIAL
    total_floors: int = 1
    total_units: int = 0
    address: str = ""
    status: BuildingStatus = BuildingStatus.ACTIVE


# ═══════════════════════════════════════════════════════════════
# Room · 房号实体
# ═══════════════════════════════════════════════════════════════

@dataclass
class Room:
    """
    房号实体

    对应表: community_rooms

    SQL:
        CREATE TABLE community_rooms (
            room_id           VARCHAR(32)    PRIMARY KEY,
            building_id       VARCHAR(32)    NOT NULL REFERENCES community_buildings(building_id),
            room_number       VARCHAR(16)    NOT NULL,
            room_area         NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (room_area >= 0),
            property_fee_rate NUMERIC(10,4)  NOT NULL DEFAULT 0
                CHECK (property_fee_rate >= 0),
            parking_spots     INTEGER        NOT NULL DEFAULT 0
                CHECK (parking_spots >= 0),
            parking_fee_rate  NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (parking_fee_rate >= 0),
            status            VARCHAR(16)    NOT NULL DEFAULT 'OCCUPIED'
                CHECK (status IN ('OCCUPIED', 'VACANT', 'DECORATING')),
            created_at        TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (building_id, room_number)
        );

        SELECT * FROM community_rooms WHERE room_id = :room_id;
    """
    room_id: str
    building_id: str
    room_number: str
    room_area: float = 0.0
    property_fee_rate: float = 0.0            # 物业费单价（元/㎡·月）
    parking_spots: int = 0
    parking_fee_rate: float = 0.0             # 车位费单价（元/个·月）
    status: RoomStatus = RoomStatus.OCCUPIED


# ═══════════════════════════════════════════════════════════════
# User · 用户实体
# ═══════════════════════════════════════════════════════════════

@dataclass
class User:
    """
    用户实体

    对应表: sys_users

    SQL:
        CREATE TABLE sys_users (
            user_id     VARCHAR(32)  PRIMARY KEY,
            user_name   VARCHAR(64)  NOT NULL,
            role        VARCHAR(16)  NOT NULL DEFAULT 'owner'
                CHECK (role IN ('owner', 'staff', 'admin')),
            building_id VARCHAR(32)  REFERENCES community_buildings(building_id),
            room_id     VARCHAR(32)  REFERENCES community_rooms(room_id),
            phone       VARCHAR(20),
            email       VARCHAR(128),
            status      VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'INACTIVE', 'FROZEN')),
            created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        SELECT * FROM sys_users WHERE user_id = :user_id;
    """
    user_id: str
    user_name: str
    role: UserRole = UserRole.OWNER
    building_id: Optional[str] = None
    room_id: Optional[str] = None
    phone: str = ""
    email: str = ""
    status: UserStatus = UserStatus.ACTIVE

    # 关联（懒加载，由 Repository 填充）
    building_name: str = ""
    room_number: str = ""

    def is_owner(self) -> bool:
        """
        判断是否为业主

        SQL:
            SELECT role FROM sys_users WHERE user_id = :user_id;
            -- 应用层判断: role = 'owner'
        """
        return self.role == UserRole.OWNER

    def is_staff(self) -> bool:
        """
        判断是否为物业员工

        SQL:
            SELECT role FROM sys_users WHERE user_id = :user_id;
            -- 应用层判断: role = 'staff'
        """
        return self.role == UserRole.PROPERTY_STAFF

    def is_admin(self) -> bool:
        """
        判断是否为管理员

        SQL:
            SELECT role FROM sys_users WHERE user_id = :user_id;
            -- 应用层判断: role = 'admin'
        """
        return self.role == UserRole.COMMUNITY_ADMIN


# ═══════════════════════════════════════════════════════════════
# Bill · 账单实体（聚合根）
# ═══════════════════════════════════════════════════════════════

@dataclass
class Bill:
    """
    账单实体（聚合根）

    核心业务数据，包含完整的费用明细和状态。
    对应表: fee_bills

    SQL:
        CREATE TABLE fee_bills (
            bill_id       VARCHAR(32)    PRIMARY KEY,
            user_id       VARCHAR(32)    NOT NULL REFERENCES sys_users(user_id),
            room_id       VARCHAR(32)    NOT NULL REFERENCES community_rooms(room_id),
            bill_period   VARCHAR(7)     NOT NULL,
            property_fee  NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (property_fee >= 0),
            utility_fee   NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (utility_fee >= 0),
            parking_fee   NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (parking_fee >= 0),
            late_fee      NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (late_fee >= 0),
            total_amount  NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (total_amount >= 0),
            due_date      DATE           NOT NULL,
            status        VARCHAR(16)    NOT NULL DEFAULT 'UNPAID'
                CHECK (status IN ('UNPAID', 'OVERDUE', 'PAID', 'CANCELLED')),
            payment_time  TIMESTAMP,
            receipt_no    VARCHAR(32),
            created_at    TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, bill_period)
        );
    """
    bill_id: str
    user_id: str
    room_id: str
    bill_period: str
    property_fee: float = 0.0
    utility_fee: float = 0.0
    parking_fee: float = 0.0
    late_fee: float = 0.0
    total_amount: float = 0.0
    due_date: str = ""
    status: BillStatus = BillStatus.UNPAID
    payment_time: Optional[str] = None
    receipt_no: Optional[str] = None

    # 关联（懒加载，由 Repository 填充）
    user_name: str = ""
    building_name: str = ""
    room_number: str = ""

    def is_paid(self) -> bool:
        """
        判断是否已缴费

        SQL:
            SELECT status FROM fee_bills WHERE bill_id = :bill_id;
            -- 应用层判断: status = 'PAID'
        """
        return self.status == BillStatus.PAID

    def is_overdue(self, today: date | None = None) -> bool:
        """
        判断是否已逾期

        SQL:
            SELECT status, due_date FROM fee_bills WHERE bill_id = :bill_id;
            -- 应用层判断: status != 'PAID' AND CURRENT_DATE > due_date
        """
        if self.status == BillStatus.PAID:
            return False
        if today is None:
            today = date.today()
        return today > date.fromisoformat(self.due_date)

    def overdue_days(self, today: date | None = None) -> int:
        """
        计算逾期天数

        SQL:
            SELECT (CURRENT_DATE - due_date) AS overdue_days
              FROM fee_bills
             WHERE bill_id = :bill_id AND status != 'PAID';
        """
        if not self.is_overdue(today):
            return 0
        if today is None:
            today = date.today()
        return (today - date.fromisoformat(self.due_date)).days

    def fee_detail(self) -> FeeDetail:
        """
        获取费用明细

        SQL:
            SELECT property_fee, utility_fee, parking_fee, late_fee
              FROM fee_bills
             WHERE bill_id = :bill_id;
        """
        return FeeDetail(
            property_fee=Money.from_float(self.property_fee),
            utility_fee=Money.from_float(self.utility_fee),
            parking_fee=Money.from_float(self.parking_fee),
            late_fee=Money.from_float(self.late_fee),
        )


# ═══════════════════════════════════════════════════════════════
# Payment · 缴费记录实体
# ═══════════════════════════════════════════════════════════════

@dataclass
class Payment:
    """
    缴费记录实体

    对应表: fee_payments

    SQL:
        CREATE TABLE fee_payments (
            payment_id     VARCHAR(32)    PRIMARY KEY,
            bill_id        VARCHAR(32)    NOT NULL REFERENCES fee_bills(bill_id),
            user_id        VARCHAR(32)    NOT NULL REFERENCES sys_users(user_id),
            pay_amount     NUMERIC(10,2)  NOT NULL
                CHECK (pay_amount > 0),
            pay_method     VARCHAR(16)    NOT NULL DEFAULT 'WECHAT'
                CHECK (pay_method IN ('WECHAT', 'ALIPAY', 'BANK_CARD', 'CASH', 'OFFLINE')),
            pay_status     VARCHAR(16)    NOT NULL DEFAULT 'SUCCESS'
                CHECK (pay_status IN ('PENDING', 'SUCCESS', 'FAILED', 'REFUNDED')),
            transaction_id VARCHAR(64),
            receipt_no     VARCHAR(32),
            paid_at        TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at     TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        SELECT * FROM fee_payments WHERE payment_id = :payment_id;
    """
    payment_id: str
    bill_id: str
    user_id: str
    pay_amount: float
    pay_method: PayMethod = PayMethod.WECHAT
    pay_status: PayStatus = PayStatus.SUCCESS
    transaction_id: str = ""
    receipt_no: str = ""
    paid_at: Optional[str] = None
    updated_at: Optional[str] = None

    # 关联（懒加载，由 Repository 填充）
    user_name: str = ""

    def is_successful(self) -> bool:
        """
        判断支付是否成功

        SQL:
            SELECT pay_status FROM fee_payments WHERE payment_id = :payment_id;
            -- 应用层判断: pay_status = 'SUCCESS'
        """
        return self.pay_status == PayStatus.SUCCESS

    def is_refunded(self) -> bool:
        """
        判断是否已退款

        SQL:
            SELECT pay_status FROM fee_payments WHERE payment_id = :payment_id;
            -- 应用层判断: pay_status = 'REFUNDED'
        """
        return self.pay_status == PayStatus.REFUNDED


# ═══════════════════════════════════════════════════════════════
# Receipt · 电子票据实体
# ═══════════════════════════════════════════════════════════════

@dataclass
class Receipt:
    """
    电子票据实体

    对应表: fee_receipts

    SQL:
        CREATE TABLE fee_receipts (
            receipt_no   VARCHAR(32)    PRIMARY KEY,
            bill_id      VARCHAR(32)    NOT NULL REFERENCES fee_bills(bill_id),
            user_id      VARCHAR(32)    NOT NULL REFERENCES sys_users(user_id),
            payment_id   VARCHAR(32)    NOT NULL REFERENCES fee_payments(payment_id),
            period       VARCHAR(7)     NOT NULL,
            property_fee NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (property_fee >= 0),
            utility_fee  NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (utility_fee >= 0),
            parking_fee  NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (parking_fee >= 0),
            late_fee     NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (late_fee >= 0),
            total_amount NUMERIC(10,2)  NOT NULL DEFAULT 0
                CHECK (total_amount >= 0),
            issue_time   TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_valid     BOOLEAN        NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        SELECT * FROM fee_receipts WHERE receipt_no = :receipt_no;
    """
    receipt_no: str
    bill_id: str
    user_id: str
    payment_id: str
    period: str
    property_fee: float = 0.0
    utility_fee: float = 0.0
    parking_fee: float = 0.0
    late_fee: float = 0.0
    total_amount: float = 0.0
    issue_time: str = ""
    is_valid: bool = True

    # 关联
    user_name: str = ""
    building_name: str = ""
    room_number: str = ""
    payment_time: str = ""