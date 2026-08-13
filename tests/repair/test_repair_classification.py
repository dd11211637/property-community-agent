from property_agent.repair.domain.classification import classify_repair_category
from property_agent.repair.domain.enums import RepairCategory


def test_classifies_resident_symptoms_without_requiring_enum_names():
    cases = {
        "厨房水管接头一直渗水": RepairCategory.WATER_PLUMBING,
        "客厅插座一用就跳闸": RepairCategory.ELECTRICAL,
        "电梯门关不上还有异响": RepairCategory.ELEVATOR,
        "卧室窗户把手断了": RepairCategory.OTHER,
    }

    for description, expected in cases.items():
        assert classify_repair_category(description) == expected
