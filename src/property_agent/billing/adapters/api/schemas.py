from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bill_id: str
    bill_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    property_fee: Decimal
    utility_fee: Decimal
    parking_fee: Decimal
    late_fee: Decimal
    total_amount: Decimal
    due_date: date | str
    status: str
    payment_time: datetime | str | None = None
    receipt_no: str | None = None
    community_id: str | None = None
    house_id: str | None = None
    version: int = Field(ge=1)
    fee_type: str | None = None
    source_time: datetime | str | None = None
    rule_version: str | None = None
    rule_name: str | None = None
    user_name: str = ""
    building_name: str = ""
    room_number: str = ""


class BillingRuleResponse(BaseModel):
    id: str
    community_id: str
    fee_type: str
    version: str
    name: str
    parameters: dict[str, Any] | None = None
    valid_from: datetime | str | None = None
    valid_until: datetime | str | None = None


class BillDetailResponse(BaseModel):
    bill: BillResponse
    rule: BillingRuleResponse | None
    unknown_rule: bool
    consultation_entry: str | None = None


class BillingRuleLookupResponse(BaseModel):
    fee_type: str
    rule: BillingRuleResponse | None
    unknown_rule: bool
    consultation_entry: str | None = None


class ConsultationResponse(BaseModel):
    id: str
    community_id: str
    actor_id: str
    subject: str
    description: str
    house_id: str | None = None
    bill_id: str | None = None
    status: str
    answer: str | None = None
    handler_id: str | None = None
    version: int = Field(ge=1)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
