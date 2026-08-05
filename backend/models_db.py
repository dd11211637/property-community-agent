"""
SQLAlchemy ORM 模型 - 物业社区管理AI智能体 · 费用查询与智能缴费模块

对应 PostgreSQL DDL (sql/ddl.sql)，同时兼容 SQLite 开发环境。
表结构: 6 张表 + 完整的索引、约束、外键关系。
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

class Building(Base):
    __tablename__ = "community_buildings"

    building_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="楼栋唯一标识")
    building_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="楼栋名称")
    building_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="RESIDENTIAL", comment="楼栋类型"
    )
    total_floors: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="总楼层数")
    total_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="总户数/单元数")
    address: Mapped[Optional[str]] = mapped_column(String(256), comment="地址")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE", comment="状态"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关系
    rooms: Mapped[List["Room"]] = relationship("Room", back_populates="building", lazy="selectin")
    users: Mapped[List["User"]] = relationship("User", back_populates="building_ref", lazy="selectin")

    __table_args__ = (
        CheckConstraint("building_type IN ('RESIDENTIAL', 'COMMERCIAL', 'OFFICE')", name="ck_building_type"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE')", name="ck_building_status"),
        CheckConstraint("total_floors > 0", name="ck_total_floors"),
        CheckConstraint("total_units >= 0", name="ck_total_units"),
    )


# ── 2. 房号信息表 ────────────────────────────────────

class Room(Base):
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
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OCCUPIED", comment="状态"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关系
    building: Mapped["Building"] = relationship("Building", back_populates="rooms")
    users: Mapped[List["User"]] = relationship("User", back_populates="room_ref", lazy="selectin")
    bills: Mapped[List["Bill"]] = relationship("Bill", back_populates="room_ref", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("building_id", "room_number", name="uq_room_building"),
        CheckConstraint("room_area >= 0", name="ck_room_area"),
        CheckConstraint("property_fee_rate >= 0", name="ck_property_fee_rate"),
        CheckConstraint("parking_spots >= 0", name="ck_parking_spots"),
        CheckConstraint("parking_fee_rate >= 0", name="ck_parking_fee_rate"),
        CheckConstraint("status IN ('OCCUPIED', 'VACANT', 'DECORATING')", name="ck_room_status"),
    )


# ── 3. 用户表 ─────────────────────────────────────────

class User(Base):
    __tablename__ = "sys_users"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="用户唯一标识")
    user_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="用户姓名")
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="owner", comment="角色: owner/staff/admin"
    )
    building_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("community_buildings.building_id"), comment="所属楼栋"
    )
    room_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("community_rooms.room_id"), comment="所属房号"
    )
    phone: Mapped[Optional[str]] = mapped_column(String(20), comment="手机号")
    email: Mapped[Optional[str]] = mapped_column(String(128), comment="邮箱")
    id_card: Mapped[Optional[str]] = mapped_column(String(18), comment="身份证号")
    face_img_url: Mapped[Optional[str]] = mapped_column(String(512), comment="人脸识别照片")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE", comment="状态"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关系
    building_ref: Mapped[Optional["Building"]] = relationship("Building", back_populates="users")
    room_ref: Mapped[Optional["Room"]] = relationship("Room", back_populates="users")
    bills: Mapped[List["Bill"]] = relationship("Bill", back_populates="user_ref", lazy="selectin")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="user_ref", lazy="selectin")
    receipts: Mapped[List["Receipt"]] = relationship("Receipt", back_populates="user_ref", lazy="selectin")

    __table_args__ = (
        CheckConstraint("role IN ('owner', 'staff', 'admin')", name="ck_user_role"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'FROZEN')", name="ck_user_status"),
        Index("idx_users_role", "role"),
    )


# ── 4. 账单主表 ───────────────────────────────────────

class Bill(Base):
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
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UNPAID", comment="状态"
    )
    payment_time: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="缴费时间")
    receipt_no: Mapped[Optional[str]] = mapped_column(String(32), comment="关联票据号")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关系
    user_ref: Mapped["User"] = relationship("User", back_populates="bills")
    room_ref: Mapped["Room"] = relationship("Room", back_populates="bills")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="bill_ref", lazy="selectin")
    receipts: Mapped[List["Receipt"]] = relationship("Receipt", back_populates="bill_ref", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("user_id", "bill_period", name="uq_bill_period"),
        CheckConstraint("property_fee >= 0", name="ck_property_fee"),
        CheckConstraint("utility_fee >= 0", name="ck_utility_fee"),
        CheckConstraint("parking_fee >= 0", name="ck_parking_fee"),
        CheckConstraint("late_fee >= 0", name="ck_late_fee"),
        CheckConstraint("total_amount >= 0", name="ck_total_amount"),
        CheckConstraint("status IN ('UNPAID', 'OVERDUE', 'PAID', 'CANCELLED')", name="ck_bill_status"),
        Index("idx_bills_user_id", "user_id"),
        Index("idx_bills_status", "status"),
        Index("idx_bills_due_date", "due_date"),
        Index("idx_bills_period", "bill_period"),
    )


# ── 5. 缴费记录表 ─────────────────────────────────────

class Payment(Base):
    __tablename__ = "fee_payments"

    payment_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="支付记录ID")
    bill_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("fee_bills.bill_id"), nullable=False, comment="关联账单ID"
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sys_users.user_id"), nullable=False, comment="缴费用户ID"
    )
    pay_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, comment="支付金额")
    pay_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="WECHAT", comment="支付方式"
    )
    pay_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SUCCESS", comment="支付状态"
    )
    transaction_id: Mapped[Optional[str]] = mapped_column(String(64), comment="第三方支付流水号")
    receipt_no: Mapped[Optional[str]] = mapped_column(String(32), comment="关联票据号")
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="实际支付时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="创建时间")

    # 关系
    bill_ref: Mapped["Bill"] = relationship("Bill", back_populates="payments")
    user_ref: Mapped["User"] = relationship("User", back_populates="payments")
    receipt: Mapped[Optional["Receipt"]] = relationship("Receipt", back_populates="payment_ref", uselist=False)

    __table_args__ = (
        CheckConstraint("pay_amount > 0", name="ck_pay_amount"),
        CheckConstraint(
            "pay_method IN ('WECHAT', 'ALIPAY', 'BANK_CARD', 'CASH', 'OFFLINE')",
            name="ck_pay_method"
        ),
        CheckConstraint(
            "pay_status IN ('PENDING', 'SUCCESS', 'FAILED', 'REFUNDED')",
            name="ck_pay_status"
        ),
        Index("idx_payments_user_id", "user_id"),
        Index("idx_payments_bill_id", "bill_id"),
    )


# ── 6. 电子票据表 ─────────────────────────────────────

class Receipt(Base):
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
    issue_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="开票时间")
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否有效")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), comment="创建时间")

    # 关系
    bill_ref: Mapped["Bill"] = relationship("Bill", back_populates="receipts")
    user_ref: Mapped["User"] = relationship("User", back_populates="receipts")
    payment_ref: Mapped["Payment"] = relationship("Payment", back_populates="receipt")

    __table_args__ = (
        Index("idx_receipts_user_id", "user_id"),
        Index("idx_receipts_bill_id", "bill_id"),
    )