"""
domain/value_objects.py     值对象（不可变，无标识符）

值对象由属性值定义相等性，无独立生命周期。
对应数据库中的列类型和约束。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Money:
    """
    金额值对象，精确到分

    对应 SQL 类型: NUMERIC(10,2)

    SQL:
        -- 所有金额字段在数据库中的定义:
        property_fee  NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (property_fee >= 0),
        utility_fee   NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (utility_fee >= 0),
        parking_fee   NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (parking_fee >= 0),
        late_fee      NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (late_fee >= 0),
        total_amount  NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
        pay_amount    NUMERIC(10,2) NOT NULL        CHECK (pay_amount > 0);
    """
    amount: Decimal

    def __post_init__(self):
        object.__setattr__(self, "amount", Decimal(str(self.amount)).quantize(Decimal("0.01")))

    def __add__(self, other: Money) -> Money:
        """SQL: SELECT :a + :b AS total;"""
        return Money(self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        """SQL: SELECT :a - :b AS diff;"""
        return Money(self.amount - other.amount)

    def __mul__(self, factor: float) -> Money:
        """SQL: SELECT :amount * :factor AS result;"""
        return Money(self.amount * Decimal(str(factor)))

    def __neg__(self) -> Money:
        return Money(-self.amount)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.amount == other.amount

    def __lt__(self, other: Money) -> bool:
        return self.amount < other.amount

    def __gt__(self, other: Money) -> bool:
        return self.amount > other.amount

    def is_zero(self) -> bool:
        """SQL: SELECT CASE WHEN amount = 0 THEN 1 ELSE 0 END FROM ...;"""
        return self.amount == Decimal("0")

    def to_float(self) -> float:
        return float(self.amount)

    @classmethod
    def zero(cls) -> Money:
        """SQL: SELECT 0.00 AS amount;"""
        return cls(Decimal("0"))

    @classmethod
    def from_float(cls, value: float) -> Money:
        """
        从 float 创建 Money 实例。

        SQL: SELECT CAST(:value AS NUMERIC(10,2)) AS amount;
        """
        return cls(Decimal(str(value)))


@dataclass(frozen=True)
class FeeDetail:
    """
    费用明细值对象

    对应 SQL: fee_bills 表中的费用列

    SQL:
        SELECT property_fee, utility_fee, parking_fee, late_fee
          FROM fee_bills
         WHERE bill_id = :bill_id;
    """
    property_fee: Money = field(default_factory=Money.zero)   # 物业费
    utility_fee: Money = field(default_factory=Money.zero)    # 公摊水电费
    parking_fee: Money = field(default_factory=Money.zero)    # 车位费
    late_fee: Money = field(default_factory=Money.zero)       # 滞纳金

    def total(self) -> Money:
        """
        计算费用合计

        SQL:
            SELECT (property_fee + utility_fee + parking_fee + late_fee) AS total_amount
              FROM fee_bills
             WHERE bill_id = :bill_id;
        """
        return self.property_fee + self.utility_fee + self.parking_fee + self.late_fee


@dataclass(frozen=True)
class BillPeriod:
    """
    账期值对象，格式 YYYY-MM

    对应 SQL 类型: VARCHAR(7)

    SQL:
        bill_period VARCHAR(7) NOT NULL,
        -- 约束: 格式 YYYY-MM, 范围 2020-01 ~ 2100-12

        SELECT * FROM fee_bills WHERE bill_period = :period;
        SELECT * FROM fee_bills WHERE bill_period BETWEEN :start AND :end;
    """
    value: str

    def __post_init__(self):
        if not (len(self.value) == 7 and self.value[4] == "-"):
            raise ValueError(f"账期格式错误: {self.value}，应为 YYYY-MM")
        year, month = int(self.value[:4]), int(self.value[5:])
        if not (2020 <= year <= 2100 and 1 <= month <= 12):
            raise ValueError(f"账期超出范围: {self.value}")

    @property
    def year(self) -> int:
        """SQL: SELECT CAST(SUBSTR(bill_period, 1, 4) AS INTEGER) FROM fee_bills;"""
        return int(self.value[:4])

    @property
    def month(self) -> int:
        """SQL: SELECT CAST(SUBSTR(bill_period, 6, 2) AS INTEGER) FROM fee_bills;"""
        return int(self.value[5:])

    def next_period(self) -> BillPeriod:
        """SQL: 应用层计算，等价于 DATEADD 逻辑"""
        if self.month == 12:
            return BillPeriod(f"{self.year + 1}-01")
        return BillPeriod(f"{self.year}-{self.month + 1:02d}")

    def prev_period(self) -> BillPeriod:
        """SQL: 应用层计算，等价于 DATEADD 逻辑"""
        if self.month == 1:
            return BillPeriod(f"{self.year - 1}-12")
        return BillPeriod(f"{self.year}-{self.month - 1:02d}")


@dataclass(frozen=True)
class Address:
    """
    地址值对象

    对应 SQL:
        building_name VARCHAR(64)  -- community_buildings.building_name
        room_number   VARCHAR(16)  -- community_rooms.room_number
        address       VARCHAR(256) -- community_buildings.address

        SELECT b.building_name, r.room_number, b.address
          FROM community_rooms r
          JOIN community_buildings b ON r.building_id = b.building_id
         WHERE r.room_id = :room_id;
    """
    building_name: str
    room_number: str
    detail: str = ""

    def full_address(self) -> str:
        """SQL: SELECT building_name || ' ' || room_number || ' ' || address FROM ...;"""
        parts = [self.building_name, self.room_number]
        if self.detail:
            parts.append(self.detail)
        return " ".join(parts)