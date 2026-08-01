from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from property_agent.billing.application.queries import BillSearch
from property_agent.billing.domain.entities import Bill
from property_agent.billing.domain.enums import FeeType, PaymentStatus
from property_agent.billing.infrastructure.models import BillModel


class SqlAlchemyBillRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(
        self,
        community_id: UUID,
        search: BillSearch,
        *,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> list[Bill]:
        statement = self._filtered_statement(community_id, search, allowed_house_ids)
        statement = (
            statement.order_by(BillModel.period_end.desc(), BillModel.created_at.desc())
            .offset(search.offset)
            .limit(search.limit)
        )
        return [self._to_domain(model) for model in self._session.scalars(statement).all()]

    def count(
        self,
        community_id: UUID,
        search: BillSearch,
        *,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> int:
        statement = self._filtered_statement(community_id, search, allowed_house_ids)
        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        return int(self._session.scalar(count_statement) or 0)

    def get(self, bill_id: UUID, community_id: UUID) -> Bill | None:
        model = self._session.scalar(
            select(BillModel).where(
                BillModel.id == bill_id,
                BillModel.community_id == community_id,
            )
        )
        return self._to_domain(model) if model else None

    @staticmethod
    def _filtered_statement(
        community_id: UUID,
        search: BillSearch,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> Select[tuple[BillModel]]:
        statement = select(BillModel).where(BillModel.community_id == community_id)
        if allowed_house_ids is not None:
            if not allowed_house_ids:
                return statement.where(False)
            statement = statement.where(BillModel.house_id.in_(allowed_house_ids))
        if search.fee_types:
            statement = statement.where(
                BillModel.fee_type.in_([fee_type.value for fee_type in search.fee_types])
            )
        if search.period_from is not None:
            statement = statement.where(BillModel.period_end >= search.period_from)
        if search.period_to is not None:
            statement = statement.where(BillModel.period_start <= search.period_to)
        return statement

    @staticmethod
    def _to_domain(model: BillModel) -> Bill:
        return Bill(
            id=model.id,
            community_id=model.community_id,
            external_bill_no=model.external_bill_no,
            house_id=model.house_id,
            fee_type=FeeType(model.fee_type),
            period_start=model.period_start,
            period_end=model.period_end,
            amount=model.amount,
            detail=dict(model.detail_json),
            payment_status=PaymentStatus(model.payment_status),
            source_system=model.source_system,
            source_updated_at=model.source_updated_at,
            created_at=model.created_at,
        )
