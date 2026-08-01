from typing import Any

from property_agent.billing.domain.entities import Bill, BillExplanation


def bill_data(bill: Bill) -> dict[str, Any]:
    return {
        "id": str(bill.id),
        "community_id": str(bill.community_id),
        "external_bill_no": bill.external_bill_no,
        "house_id": str(bill.house_id),
        "fee_type": bill.fee_type.value,
        "period_start": bill.period_start.isoformat(),
        "period_end": bill.period_end.isoformat(),
        "amount": str(bill.amount),
        "detail": bill.detail,
        "payment_status": bill.payment_status.value,
        "source_system": bill.source_system,
        "source_updated_at": bill.source_updated_at.isoformat(),
        "created_at": bill.created_at.isoformat(),
    }


def explanation_data(explanation: BillExplanation) -> dict[str, Any]:
    return {
        "bill_id": str(explanation.bill_id),
        "rule_name": explanation.rule_name,
        "rule_version": explanation.rule_version,
        "explanation": explanation.explanation,
        "source_system": explanation.source_system,
        "source_updated_at": explanation.source_updated_at.isoformat(),
    }
