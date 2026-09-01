from property_agent.inspection.domain.classification import normalize_security_event
from property_agent.inspection.domain.enums import EventRiskLevel, EventType


def test_egress_obstruction_maps_to_existing_compatible_event_type():
    normalized = normalize_security_event("消防通道有两辆电动车堵着")

    assert normalized.event_type is EventType.EQUIPMENT_FAULT
    assert normalized.risk_level is EventRiskLevel.MEDIUM


def test_severe_egress_obstruction_has_deterministic_high_risk_floor():
    normalized = normalize_security_event(
        "安全出口堆了很多纸箱，已经不能通行",
        requested_risk="LOW",
    )

    assert normalized.event_type is EventType.EQUIPMENT_FAULT
    assert normalized.risk_level is EventRiskLevel.HIGH_RISK


def test_user_cannot_lower_gas_leak_risk_floor():
    normalized = normalize_security_event("地下车库有很浓的燃气味", requested_risk="LOW")

    assert normalized.event_type is EventType.GAS_LEAK
    assert normalized.risk_level is EventRiskLevel.HIGH_RISK
