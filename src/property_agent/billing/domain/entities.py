from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from property_agent.billing.domain.enums import FeeType, PaymentStatus

MONEY_QUANTUM = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Bill:
    id: UUID
    community_id: UUID
    external_bill_no: str
    house_id: UUID
    fee_type: FeeType
    period_start: date
    period_end: date
    amount: Decimal
    detail: dict[str, Any]
    payment_status: PaymentStatus
    source_system: str
    source_updated_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        amount = Decimal(self.amount).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        if amount < 0:
            raise ValueError("bill amount must not be negative")
        if self.period_end < self.period_start:
            raise ValueError("bill period_end must not precede period_start")
        if not self.external_bill_no.strip():
            raise ValueError("external_bill_no is required")
        if not self.source_system.strip():
            raise ValueError("source_system is required")
        object.__setattr__(self, "amount", amount)


@dataclass(frozen=True, slots=True)
class BillExplanation:
    bill_id: UUID
    rule_name: str
    rule_version: str
    explanation: str
    source_system: str
    source_updated_at: datetime
