from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_agent.billing.adapters.api.router import bill_data
from property_agent.billing.domain.entities import Bill
from property_agent.billing.infrastructure.orm_models import BillModel
from property_agent.platform.infrastructure.orm_models import Base


def _bill(**overrides):
    values = {
        "bill_id": "bill-decimal",
        "user_id": "user-1",
        "room_id": "room-1",
        "bill_period": "2026-08",
        "property_fee": Decimal("100.10"),
        "utility_fee": Decimal("20.20"),
        "parking_fee": Decimal("30.30"),
        "late_fee": Decimal("0.40"),
        "total_amount": Decimal("151.00"),
        "due_date": "2026-08-31",
    }
    values.update(overrides)
    return Bill(**values)


def test_bill_preserves_decimal_and_enforces_component_total():
    bill = _bill(property_fee="100.105", total_amount="151.01")

    assert bill.property_fee == Decimal("100.11")
    assert bill.total_amount == Decimal("151.01")


def test_bill_rejects_inconsistent_total():
    with pytest.raises(ValueError, match="sum of all fee components"):
        _bill(total_amount="999.99")


def test_bill_response_serializes_money_as_fixed_decimal_strings():
    payload = bill_data(_bill()).model_dump(mode="json")

    assert payload["property_fee"] == "100.10"
    assert payload["total_amount"] == "151.00"


def test_database_rejects_inconsistent_bill_total():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            BillModel(
                bill_id="invalid-total",
                user_id="user-1",
                room_id="room-1",
                bill_period="2026-08",
                property_fee=Decimal("10.00"),
                utility_fee=Decimal("0.00"),
                parking_fee=Decimal("0.00"),
                late_fee=Decimal("0.00"),
                total_amount=Decimal("9.99"),
                due_date=date(2026, 8, 31),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
