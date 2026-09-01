from property_agent.agent.application.slot_resolution import resolve_inspection_event_facts


def test_event_fact_reply_can_fill_multiple_values_without_intent_planning():
    updates = resolve_inspection_event_facts(
        {},
        "消防通道堆了很多纸箱和两辆电动车，基本已经不能通行。",
    )

    assert updates["location"] == "消防通道"
    assert "两辆电动车" in updates["description"]
    assert updates["event_type"] == "EQUIPMENT_FAULT"
    assert updates["risk_level"] == "HIGH_RISK"


def test_internal_category_reply_is_not_written_as_a_business_fact():
    updates = resolve_inspection_event_facts(
        {"description": "安全出口有人堆放杂物", "location": "安全出口"},
        "其他事件",
    )

    assert "description" not in updates
    assert updates["event_type"] == "EQUIPMENT_FAULT"


def test_followup_facts_extend_the_active_event_description():
    updates = resolve_inspection_event_facts(
        {"location": "地下车库"},
        "有很浓的燃气味",
    )

    assert updates["description"] == "有很浓的燃气味"
    assert updates["event_type"] == "GAS_LEAK"
    assert updates["risk_level"] == "HIGH_RISK"
