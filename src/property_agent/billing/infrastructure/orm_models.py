"""
infrastructure/orm_models.py     SQLAlchemy ORM 模型

对应 PostgreSQL DDL (sql/ddl.sql)，同时兼容 SQLite。
6 张表，完整的索引、约束、外键关系。
每个模型标注了对应的 DDL CREATE TABLE 语句。
"""
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import (
    Column, String, Integer, Numeric, Date, DateTime, Boolean, Text,
    ForeignKey, UniqueConstraint, CheckConstraint, Index, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


# ── 1. 楼栋信息表 ────────────────────────────────────

class BuildingModel(Base):
    """
    楼栋信息表

    对应 SQL:
        CREATE TABLE community_buildings (
            building_id     VARCHAR(32)     PRIMARY KEY,
            building_name   VARCHAR(64)     NOT NULL,
            building_type   VARCHAR(16)     NOT NULL DEFAULT 'RESIDENTIAL'
                            CHECK (building_type IN ('RESIDENTIAL', 'COMMERCIAL', 'OFFICE')),
            total_floors    INTEGER         NOT NULL DEFAULT 1 CHECK (total_floors > 0),
            total_units     INTEGER         NOT NULL DEFAULT 0 CHECK (total_units >= 0),
            address         VARCHAR(256),
            status          VARCHAR(16)     NOT NULL DEFAULT 'ACTIVE'
                            CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE')),
            created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """
    __tablename__ = "community_buildings"

    building_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="楼栋唯一标识")
    building_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="楼栋名称")
    building_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="RESIDENTIAL", comment="楼栋类型"
    )
    total_floors: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="总楼层数")
    total_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="总户数")
    address: Mapped[Optional[str]] = mapped_column(String(256), comment="地址")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", comment="状态")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")

    rooms: Mapped[List["RoomModel"]] = relationship("RoomModel", back_populates="building", lazy="selectin")
    users: Mapped[List["UserModel"]] = relationship("UserModel", back_populates="building_ref", lazy="selectin")


# ── 2. 房号信息表 ────────────────────────────────────

class RoomModel(Base):
    """
    房号信息表

    对应 SQL:
        CREATE TABLE community_rooms (
            room_id           VARCHAR(32)     PRIMARY KEY,
            building_id       VARCHAR(32)     NOT NULL
                              REFERENCES community_buildings(building_id),
            room_number       VARCHAR(16)     NOT NULL,
            room_area         NUMERIC(10,2)   NOT NULL DEFAULT 0 CHECK (room_area >= 0),
            property_fee_rate NUMERIC(10,4)   NOT NULL DEFAULT 0 CHECK (property_fee_rate >= 0),
            parking_spots     INTEGER         NOT NULL DEFAULT 0 CHECK (parking_spots >= 0),
            parking_fee_rate  NUMERIC(10,2)   NOT NULL DEFAULT 0 CHECK (parking_fee_rate >= 0),
            status            VARCHAR(16)     NOT NULL DEFAULT 'OCCUPIED'
                              CHECK (status IN ('OCCUPIED', 'VACANT', 'DECORATING')),
            created_at        TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (building_id, room_number)
        );
    """
    __tablename__ = "community_rooms"

    room_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="房号唯一标识")
    building_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("community_buildings.building_id"), nullable=False, comment="所属楼栋"
    )
    room_number: Mapped[str] = mapped_column(String(16), nullable=False, comment="房号")
    room_area: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="建筑面积")
    property_fee_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0, comment="物业费单价")
    parking_spots: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="绑定车位数")
    parking_fee_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="车位费单价")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OCCUPIED", comment="状态")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")

    building: Mapped["BuildingModel"] = relationship("BuildingModel", back_populates="rooms")
    users: Mapped[List["UserModel"]] = relationship("UserModel", back_populates="room_ref", lazy="selectin")
    bills: Mapped[List["BillModel"]] = relationship("BillModel", back_populates="room_ref", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("building_id", "room_number", name="uq_room_building"),
    )


# ── 3. 用户表 ─────────────────────────────────────────

class UserModel(Base):
    """
    用户表

    对应 SQL:
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
    """
    __tablename__ = "sys_users"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="用户唯一标识")
    user_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="用户姓名")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="owner", comment="角色")
    building_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("community_buildings.building_id"), comment="所属楼栋"
    )
    room_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("community_rooms.room_id"), comment="所属房号"
    )
    phone: Mapped[Optional[str]] = mapped_column(String(20), comment="手机号")
    email: Mapped[Optional[str]] = mapped_column(String(128), comment="邮箱")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", comment="状态")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")

    building_ref: Mapped[Optional["BuildingModel"]] = relationship("BuildingModel", back_populates="users")
    room_ref: Mapped[Optional["RoomModel"]] = relationship("RoomModel", back_populates="users")
    bills: Mapped[List["BillModel"]] = relationship("BillModel", back_populates="user_ref", lazy="selectin")
    payments: Mapped[List["PaymentModel"]] = relationship("PaymentModel", back_populates="user_ref", lazy="selectin")
    receipts: Mapped[List["ReceiptModel"]] = relationship("ReceiptModel", back_populates="user_ref", lazy="selectin")


# ── 4. 账单主表 ───────────────────────────────────────

class BillModel(Base):
    """
    账单主表

    对应 SQL:
        CREATE TABLE fee_bills (
            bill_id       VARCHAR(32)    PRIMARY KEY,
            user_id       VARCHAR(32)    NOT NULL REFERENCES sys_users(user_id),
            room_id       VARCHAR(32)    NOT NULL REFERENCES community_rooms(room_id),
            bill_period   VARCHAR(7)     NOT NULL,
            property_fee  NUMERIC(10,2)  NOT NULL DEFAULT 0 CHECK (property_fee >= 0),
            utility_fee   NUMERIC(10,2)  NOT NULL DEFAULT 0 CHECK (utility_fee >= 0),
            parking_fee   NUMERIC(10,2)  NOT NULL DEFAULT 0 CHECK (parking_fee >= 0),
            late_fee      NUMERIC(10,2)  NOT NULL DEFAULT 0 CHECK (late_fee >= 0),
            total_amount  NUMERIC(10,2)  NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
            due_date      DATE           NOT NULL,
            status        VARCHAR(16)    NOT NULL DEFAULT 'UNPAID'
                          CHECK (status IN ('UNPAID', 'OVERDUE', 'PAID', 'CANCELLED')),
            payment_time  TIMESTAMP,
            receipt_no    VARCHAR(32),
            created_at    TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, bill_period)
        ) PARTITION BY RANGE (due_date);
    """
    __tablename__ = "fee_bills"

    bill_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="账单唯一标识")
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sys_users.user_id"), nullable=False, comment="业主ID"
    )
    room_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("community_rooms.room_id"), nullable=False, comment="房号ID"
    )
    bill_period: Mapped[str] = mapped_column(String(7), nullable=False, comment="账期 YYYY-MM")
    property_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="物业费")
    utility_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="公摊水电费")
    parking_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="车位费")
    late_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="滞纳金")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="合计金额")
    due_date: Mapped[date] = mapped_column(Date, nullable=False, comment="最迟缴费日")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNPAID", comment="状态")
    payment_time: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="缴费时间")
    receipt_no: Mapped[Optional[str]] = mapped_column(String(32), comment="关联票据号")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")

    user_ref: Mapped["UserModel"] = relationship("UserModel", back_populates="bills")
    room_ref: Mapped["RoomModel"] = relationship("RoomModel", back_populates="bills")
    payments: Mapped[List["PaymentModel"]] = relationship("PaymentModel", back_populates="bill_ref", lazy="selectin")
    receipts: Mapped[List["ReceiptModel"]] = relationship("ReceiptModel", back_populates="bill_ref", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("user_id", "bill_period", name="uq_bill_period"),
        Index("idx_bills_user_id", "user_id"),
        Index("idx_bills_status", "status"),
        Index("idx_bills_due_date", "due_date"),
        Index("idx_bills_period", "bill_period"),
    )


# ── 5. 缴费记录表 ─────────────────────────────────────

class PaymentModel(Base):
    """
    缴费记录表

    对应 SQL:
        CREATE TABLE fee_payments (
            payment_id     VARCHAR(32)    PRIMARY KEY,
            bill_id        VARCHAR(32)    NOT NULL REFERENCES fee_bills(bill_id),
            user_id        VARCHAR(32)    NOT NULL REFERENCES sys_users(user_id),
            pay_amount     NUMERIC(10,2)  NOT NULL CHECK (pay_amount > 0),
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
    """
    __tablename__ = "fee_payments"

    payment_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="支付记录ID")
    bill_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("fee_bills.bill_id"), nullable=False, comment="关联账单ID"
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sys_users.user_id"), nullable=False, comment="缴费用户ID"
    )
    pay_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, comment="支付金额")
    pay_method: Mapped[str] = mapped_column(String(16), nullable=False, default="WECHAT", comment="支付方式")
    pay_status: Mapped[str] = mapped_column(String(16), nullable=False, default="SUCCESS", comment="支付状态")
    transaction_id: Mapped[Optional[str]] = mapped_column(String(64), comment="第三方支付流水号")
    receipt_no: Mapped[Optional[str]] = mapped_column(String(32), comment="关联票据号")
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="实际支付时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), comment="更新时间")

    bill_ref: Mapped["BillModel"] = relationship("BillModel", back_populates="payments")
    user_ref: Mapped["UserModel"] = relationship("UserModel", back_populates="payments")
    receipt: Mapped[Optional["ReceiptModel"]] = relationship(
        "ReceiptModel", back_populates="payment_ref", uselist=False
    )

    __table_args__ = (
        Index("idx_payments_user_id", "user_id"),
        Index("idx_payments_bill_id", "bill_id"),
    )


# ── 6. 电子票据表 ─────────────────────────────────────

class ReceiptModel(Base):
    """
    电子票据表

    对应 SQL:
        CREATE TABLE fee_receipts (
            receipt_no   VARCHAR(32)    PRIMARY KEY,
            bill_id      VARCHAR(32)    NOT NULL REFERENCES fee_bills(bill_id),
            user_id      VARCHAR(32)    NOT NULL REFERENCES sys_users(user_id),
            payment_id   VARCHAR(32)    NOT NULL REFERENCES fee_payments(payment_id),
            period       VARCHAR(7)     NOT NULL,
            property_fee NUMERIC(10,2)  NOT NULL DEFAULT 0,
            utility_fee  NUMERIC(10,2)  NOT NULL DEFAULT 0,
            parking_fee  NUMERIC(10,2)  NOT NULL DEFAULT 0,
            late_fee     NUMERIC(10,2)  NOT NULL DEFAULT 0,
            total_amount NUMERIC(10,2)  NOT NULL DEFAULT 0,
            issue_time   TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_valid     BOOLEAN        NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """
    __tablename__ = "fee_receipts"

    receipt_no: Mapped[str] = mapped_column(String(32), primary_key=True, comment="票据编号")
    bill_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("fee_bills.bill_id"), nullable=False, comment="关联账单ID"
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sys_users.user_id"), nullable=False, comment="业主ID"
    )
    payment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("fee_payments.payment_id"), nullable=False, comment="关联支付记录ID"
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False, comment="账期")
    property_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="物业费")
    utility_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="公摊水电费")
    parking_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="车位费")
    late_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="滞纳金")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="合计金额")
    issue_time: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="开票时间")
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否有效")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), comment="创建时间")

    bill_ref: Mapped["BillModel"] = relationship("BillModel", back_populates="receipts")
    user_ref: Mapped["UserModel"] = relationship("UserModel", back_populates="receipts")
    payment_ref: Mapped["PaymentModel"] = relationship("PaymentModel", back_populates="receipt")

    __table_args__ = (
        Index("idx_receipts_user_id", "user_id"),
        Index("idx_receipts_bill_id", "bill_id"),
    )