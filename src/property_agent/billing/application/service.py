from uuid import UUID

from property_agent.billing.application.ports import BillRepository
from property_agent.billing.application.queries import BillSearch
from property_agent.billing.domain.entities import Bill, BillExplanation
from property_agent.billing.domain.enums import FeeType
from property_agent.billing.domain.errors import forbidden, not_found, validation_error
from property_agent.platform.context import RequestContext
from property_agent.platform.roles import Role
from property_agent.platform.validation import validate_pagination

READ_ROLES = (
    Role.RESIDENT,
    Role.CUSTOMER_SERVICE,
    Role.FINANCE_STAFF,
    Role.MANAGER,
)
STAFF_ROLES = (Role.CUSTOMER_SERVICE, Role.FINANCE_STAFF, Role.MANAGER)

RULES: dict[FeeType, tuple[str, str, str]] = {
    FeeType.PROPERTY: (
        "Property service fee rule",
        "MVP-1.0",
        "The amount comes from the source billing system and is shown without modification.",
    ),
    FeeType.WATER: (
        "Water fee rule",
        "MVP-1.0",
        "The amount is the source-system water charge for the displayed billing period.",
    ),
    FeeType.ELECTRICITY: (
        "Electricity fee rule",
        "MVP-1.0",
        "The amount is the source-system electricity charge for the displayed billing period.",
    ),
    FeeType.PARKING: (
        "Parking fee rule",
        "MVP-1.0",
        "The amount is the source-system parking charge for the displayed billing period.",
    ),
}


class BillingService:
    def __init__(self, repository: BillRepository) -> None:
        self._repository = repository

    def search(self, search: BillSearch, context: RequestContext) -> tuple[list[Bill], int]:
        self._require_read_role(context)
        self._validate_search(search)
        allowed_house_ids = self._allowed_house_ids(search.house_id, context)
        if allowed_house_ids == frozenset():
            return [], 0
        bills = self._repository.list(
            context.community_id,
            search,
            allowed_house_ids=allowed_house_ids,
        )
        total = self._repository.count(
            context.community_id,
            search,
            allowed_house_ids=allowed_house_ids,
        )
        return list(bills), total

    def get(self, bill_id: UUID, context: RequestContext) -> Bill:
        self._require_read_role(context)
        bill = self._repository.get(bill_id, context.community_id)
        if bill is None or not self._can_access_house(bill.house_id, context):
            raise not_found()
        return bill

    def explain(self, bill_id: UUID, context: RequestContext) -> BillExplanation:
        bill = self.get(bill_id, context)
        rule_name, rule_version, text = RULES[bill.fee_type]
        return BillExplanation(
            bill_id=bill.id,
            rule_name=rule_name,
            rule_version=rule_version,
            explanation=text,
            source_system=bill.source_system,
            source_updated_at=bill.source_updated_at,
        )

    @staticmethod
    def _require_read_role(context: RequestContext) -> None:
        if not context.has_any_role(*READ_ROLES):
            raise forbidden()

    @staticmethod
    def _validate_search(search: BillSearch) -> None:
        validate_pagination(search.limit, search.offset)
        if (
            search.period_from is not None
            and search.period_to is not None
            and search.period_to < search.period_from
        ):
            raise validation_error("period_to must not precede period_from.")

    @staticmethod
    def _can_access_house(house_id: UUID, context: RequestContext) -> bool:
        return context.has_any_role(*STAFF_ROLES) or house_id in context.house_ids

    @staticmethod
    def _allowed_house_ids(
        requested_house_id: UUID | None,
        context: RequestContext,
    ) -> frozenset[UUID] | None:
        if context.has_any_role(*STAFF_ROLES):
            return frozenset({requested_house_id}) if requested_house_id else None
        if requested_house_id is not None and requested_house_id not in context.house_ids:
            raise forbidden()
        if requested_house_id is not None:
            return frozenset({requested_house_id})
        return context.house_ids
