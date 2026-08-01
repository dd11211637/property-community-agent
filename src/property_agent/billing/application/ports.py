from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from property_agent.billing.application.queries import BillSearch
from property_agent.billing.domain.entities import Bill


class BillRepository(Protocol):
    def list(
        self,
        community_id: UUID,
        search: BillSearch,
        *,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> Sequence[Bill]: ...

    def count(
        self,
        community_id: UUID,
        search: BillSearch,
        *,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> int: ...

    def get(self, bill_id: UUID, community_id: UUID) -> Bill | None: ...
