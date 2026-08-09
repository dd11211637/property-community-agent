"""
infrastructure/source_port.py     账单数据源端口实现（PRD 6.3）

隔离当前 SQLAlchemy 账单源与未来真实财务接口。``LocalBillingSourcePort``
只读访问 ``fee_bills``。
"""

from __future__ import annotations

from property_agent.billing.domain.entities import Bill
from property_agent.billing.infrastructure.repositories import SqlAlchemyBillRepository


class LocalBillingSourcePort:
    """本地账单数据源：基于 fee_bills 仓储。"""

    def __init__(self, repository: SqlAlchemyBillRepository) -> None:
        self._repo = repository

    def list_bills(
        self,
        *,
        community_id: str,
        house_id: str | None = None,
        fee_type: str | None = None,
        period: str | None = None,
        status: str | None = None,
    ) -> list[Bill]:
        return self._repo.find_by_community_and_house(
            community_id=community_id,
            house_id=house_id,
            fee_type=fee_type,
            period=period,
            status=status,
        )

    def get_bill(self, *, bill_id: str) -> Bill | None:
        return self._repo.find_by_id(bill_id)
