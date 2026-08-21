"""Public billing routes must remain read-only and consultation-oriented."""

from property_agent.main import create_app


def test_billing_routes_exclude_payment_mutations() -> None:
    openapi = create_app().openapi()
    paths = set(openapi["paths"])

    assert "/api/billing/bills" in paths
    assert "/api/billing/consultations" in paths
    assert not any(path.startswith("/api/bills/pay") for path in paths)
    assert "/api/bills/refund" not in paths

    list_schema = openapi["paths"]["/api/billing/bills"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    envelope_ref = list_schema["$ref"].rsplit("/", 1)[-1]
    envelope = openapi["components"]["schemas"][envelope_ref]
    bill_ref = envelope["properties"]["data"]["anyOf"][0]["items"]["$ref"].rsplit("/", 1)[-1]
    bill_schema = openapi["components"]["schemas"][bill_ref]
    assert bill_schema["properties"]["total_amount"]["type"] == "string"

    create_operation = openapi["paths"]["/api/billing/consultations"]["post"]
    request_ref = create_operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].rsplit("/", 1)[-1]
    request_schema = openapi["components"]["schemas"][request_ref]
    assert "confirmation_token" in request_schema["required"]
