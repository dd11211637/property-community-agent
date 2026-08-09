"""Public billing routes must remain read-only and consultation-oriented."""

from property_agent.main import create_app


def test_billing_routes_exclude_payment_mutations() -> None:
    paths = set(create_app().openapi()["paths"])

    assert "/api/billing/bills" in paths
    assert "/api/billing/consultations" in paths
    assert not any(path.startswith("/api/bills/pay") for path in paths)
    assert "/api/bills/refund" not in paths
