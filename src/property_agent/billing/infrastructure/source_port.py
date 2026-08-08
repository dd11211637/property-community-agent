"""
infrastructure/source_port.py     账单数据源端口实现（PRD 6.3）

隔离本地演示账单源与未来真实财务接口。``LocalBillingSourcePort`` 读取
fee_bills；``UnavailableBillingSourcePort`` 模拟外部接口中断，用于验证
R-02（接口中断时不猜测金额，仍可保存财务咨询草稿）。
"""
from __future__ import annotations

from typing import Optional

from property_agent.billing.domain.entities import Bill
from property_agent.billing.errors import BillingSourceUnavailable
from property_agent.billing.infrastructure.repositories import SqlAlchemyBillRepository


class LocalBillingSourcePort:
    """本地账单数据源：基于 fee_bills 仓储。"""

    def __init__(self, repository: SqlAlchemyBillRepository) -> None:
        self._repo = repository

    def list_bills(
        self,
        *,
        community_id: str,
        house_id: Optional[str] = None,
        fee_type: Optional[str] = None,
        period: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[Bill]:
        return self._repo.find_by_community_and_house(
            community_id=community_id,
            house_id=house_id,
            fee_type=fee_type,
            period=period,
            status=status,
        )

    def get_bill(self, *, bill_id: str) -> Optional[Bill]:
        return self._repo.find_by_id(bill_id)


class UnavailableBillingSourcePort:
    """模拟外部财务接口不可用（R-02 故障注入）。"""

    def list_bills(self, **_: object) -> list[Bill]:
        raise BillingSourceUnavailable("账单数据源暂时不可用")

    def get_bill(self, *, bill_id: str) -> Optional[Bill]:
        raise BillingSourceUnavailable("账单数据源暂时不可用")
