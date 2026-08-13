from property_agent.inspection.domain.classification import classify_security_event
from property_agent.inspection.domain.enums import EventRiskLevel, EventType


def test_classifies_event_facts_and_applies_high_risk_floor():
    assert classify_security_event("设备间闻到很重的燃气味") == (
        EventType.GAS_LEAK,
        EventRiskLevel.HIGH_RISK,
    )
    assert classify_security_event("地下车库有井盖破损") == (
        EventType.EQUIPMENT_FAULT,
        EventRiskLevel.MEDIUM,
    )
