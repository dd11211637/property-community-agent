from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from property_agent.billing.adapters.agent_tools import BillingToolAdapter
from property_agent.billing.adapters.api.dependencies import get_request_context
from property_agent.billing.application.queries import BillSearch
from property_agent.billing.application.service import BillingService
from property_agent.billing.domain.entities import Bill
from property_agent.billing.domain.enums import FeeType, PaymentStatus
from property_agent.billing.infrastructure.models import BillModel
from property_agent.billing.infrastructure.repository import SqlAlchemyBillRepository
from property_agent.main import create_app
from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.roles import Role


class FakeBillRepository:
    def __init__(self, bills: list[Bill]) -> None:
        self.bills = bills

    def _filtered(
        self,
        community_id: UUID,
        search: BillSearch,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> list[Bill]:
        bills = [bill for bill in self.bills if bill.community_id == community_id]
        if allowed_house_ids is not None:
            bills = [bill for bill in bills if bill.house_id in allowed_house_ids]
        if search.fee_types:
            bills = [bill for bill in bills if bill.fee_type in search.fee_types]
        if search.period_from:
            bills = [bill for bill in bills if bill.period_end >= search.period_from]
        if search.period_to:
            bills = [bill for bill in bills if bill.period_start <= search.period_to]
        return bills

    def list(
        self,
        community_id: UUID,
        search: BillSearch,
        *,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> list[Bill]:
        bills = self._filtered(community_id, search, allowed_house_ids)
        return bills[search.offset : search.offset + search.limit]

    def count(
        self,
        community_id: UUID,
        search: BillSearch,
        *,
        allowed_house_ids: frozenset[UUID] | None,
    ) -> int:
        return len(self._filtered(community_id, search, allowed_house_ids))

    def get(self, bill_id: UUID, community_id: UUID) -> Bill | None:
        return next(
            (
                bill
                for bill in self.bills
                if bill.id == bill_id and bill.community_id == community_id
            ),
            None,
        )


def make_bill(*, community_id: UUID, house_id: UUID, fee_type=FeeType.PROPERTY) -> Bill:
    now = datetime.now(UTC)
    return Bill(
        id=uuid4(),
        community_id=community_id,
        external_bill_no=f"DEMO-{uuid4().hex[:8]}",
        house_id=house_id,
        fee_type=fee_type,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        amount=Decimal("128.30"),
        detail={"rate": "1.20", "area": "106.92"},
        payment_status=PaymentStatus.UNPAID,
        source_system="DEMO",
        source_updated_at=now,
        created_at=now,
    )


def make_context(
    community_id: UUID,
    *,
    role: Role,
    house_ids: frozenset[UUID] = frozenset(),
) -> RequestContext:
    return RequestContext(
        actor_id=uuid4(),
        community_id=community_id,
        roles=frozenset({role}),
        house_ids=house_ids,
        request_id="req_billing",
    )


def test_resident_only_reads_bound_houses() -> None:
    community_id, own_house, other_house = uuid4(), uuid4(), uuid4()
    own_bill = make_bill(community_id=community_id, house_id=own_house)
    other_bill = make_bill(community_id=community_id, house_id=other_house)
    service = BillingService(FakeBillRepository([own_bill, other_bill]))
    context = make_context(
        community_id,
        role=Role.RESIDENT,
        house_ids=frozenset({own_house}),
    )

    bills, total = service.search(BillSearch(), context)

    assert bills == [own_bill]
    assert total == 1
    with pytest.raises(BusinessError) as exc_info:
        service.search(BillSearch(house_id=other_house), context)
    assert exc_info.value.code == "FORBIDDEN"
    with pytest.raises(BusinessError) as exc_info:
        service.get(other_bill.id, context)
    assert exc_info.value.code == "RESOURCE_NOT_FOUND"


def test_staff_is_scoped_to_trusted_community() -> None:
    community_id, other_community, house_id = uuid4(), uuid4(), uuid4()
    own_bill = make_bill(community_id=community_id, house_id=house_id)
    foreign_bill = make_bill(community_id=other_community, house_id=house_id)
    service = BillingService(FakeBillRepository([own_bill, foreign_bill]))
    context = make_context(community_id, role=Role.FINANCE_STAFF)

    bills, total = service.search(BillSearch(), context)

    assert bills == [own_bill]
    assert total == 1
    with pytest.raises(BusinessError) as exc_info:
        service.get(foreign_bill.id, context)
    assert exc_info.value.code == "RESOURCE_NOT_FOUND"


def test_explanation_cites_rule_and_source() -> None:
    community_id, house_id = uuid4(), uuid4()
    bill = make_bill(
        community_id=community_id,
        house_id=house_id,
        fee_type=FeeType.WATER,
    )
    service = BillingService(FakeBillRepository([bill]))
    context = make_context(
        community_id,
        role=Role.RESIDENT,
        house_ids=frozenset({house_id}),
    )

    explanation = service.explain(bill.id, context)

    assert explanation.rule_name
    assert explanation.rule_version == "MVP-1.0"
    assert explanation.source_system == "DEMO"


def test_public_billing_api_is_read_only_and_uses_trusted_context() -> None:
    community_id, house_id = uuid4(), uuid4()
    bill = make_bill(community_id=community_id, house_id=house_id)
    service = BillingService(FakeBillRepository([bill]))
    context = make_context(
        community_id,
        role=Role.RESIDENT,
        house_ids=frozenset({house_id}),
    )
    app = create_app(billing_service=service)
    app.dependency_overrides[get_request_context] = lambda: context
    client = TestClient(app)

    response = client.get("/api/bills")
    detail_response = client.get(f"/api/bills/{bill.id}")
    explanation_response = client.get(f"/api/bills/{bill.id}/explanation")

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["amount"] == "128.30"
    assert detail_response.json()["data"]["id"] == str(bill.id)
    assert explanation_response.json()["data"]["rule_version"] == "MVP-1.0"
    billing_operations = {
        method.upper()
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/bills")
        for method in operations
    }
    assert billing_operations == {"GET"}
    query_parameters = {
        parameter["name"]
        for parameter in app.openapi()["paths"]["/api/bills"]["get"]["parameters"]
    }
    assert "role" not in query_parameters
    assert "user_id" not in query_parameters


def test_billing_api_requires_authentication_and_service_configuration() -> None:
    service = BillingService(FakeBillRepository([]))
    unauthenticated = TestClient(create_app(billing_service=service))

    auth_response = unauthenticated.get("/api/bills")

    assert auth_response.status_code == 401
    assert auth_response.json()["error"]["code"] == "AUTH_REQUIRED"

    context = make_context(uuid4(), role=Role.FINANCE_STAFF)
    unconfigured_app = create_app()
    unconfigured_app.dependency_overrides[get_request_context] = lambda: context

    service_response = TestClient(unconfigured_app).get("/api/bills")

    assert service_response.status_code == 503
    assert service_response.json()["error"]["code"] == "ADAPTER_NOT_CONFIGURED"


def test_billing_agent_adapter_has_no_financial_write_tools() -> None:
    community_id, house_id = uuid4(), uuid4()
    bill = make_bill(community_id=community_id, house_id=house_id)
    adapter = BillingToolAdapter(BillingService(FakeBillRepository([bill])))
    context = make_context(
        community_id,
        role=Role.RESIDENT,
        house_ids=frozenset({house_id}),
    )
    public_methods = {
        name
        for name in dir(adapter)
        if not name.startswith("_") and callable(getattr(adapter, name))
    }

    assert public_methods == {"search_bills", "get_bill", "explain_bill"}
    result = adapter.search_bills(
        {
            "house_id": str(house_id),
            "fee_types": ["PROPERTY"],
            "period_from": "2026-07-01",
            "period_to": "2026-07-31",
        },
        context,
    )
    assert result["total"] == 1
    assert adapter.get_bill(str(bill.id), context)["id"] == str(bill.id)
    assert adapter.explain_bill(str(bill.id), context)["rule_version"] == "MVP-1.0"


def test_sqlalchemy_repository_applies_tenant_and_house_filters() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    BillModel.__table__.create(engine)
    community_id, other_community = uuid4(), uuid4()
    own_house, other_house = uuid4(), uuid4()
    own_bill = make_bill(community_id=community_id, house_id=own_house)
    other_bill = make_bill(community_id=community_id, house_id=other_house)
    foreign_bill = make_bill(community_id=other_community, house_id=own_house)

    with Session(engine) as session:
        for bill in (own_bill, other_bill, foreign_bill):
            session.add(
                BillModel(
                    id=bill.id,
                    community_id=bill.community_id,
                    external_bill_no=bill.external_bill_no,
                    house_id=bill.house_id,
                    fee_type=bill.fee_type.value,
                    period_start=bill.period_start,
                    period_end=bill.period_end,
                    amount=bill.amount,
                    detail_json=bill.detail,
                    payment_status=bill.payment_status.value,
                    source_system=bill.source_system,
                    source_updated_at=bill.source_updated_at,
                    created_at=bill.created_at,
                )
            )
        session.commit()
        repository = SqlAlchemyBillRepository(session)

        result = repository.list(
            community_id,
            BillSearch(),
            allowed_house_ids=frozenset({own_house}),
        )

    assert [item.id for item in result] == [own_bill.id]
    assert result[0].amount == Decimal("128.30")
