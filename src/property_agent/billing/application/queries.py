from dataclasses import dataclass
from datetime import date
from uuid import UUID

from property_agent.billing.domain.enums import FeeType


@dataclass(frozen=True, slots=True)
class BillSearch:
    house_id: UUID | None = None
    fee_types: frozenset[FeeType] = frozenset()
    period_from: date | None = None
    period_to: date | None = None
    limit: int = 50
    offset: int = 0
