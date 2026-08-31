import pytest

from property_agent.agent.repair_location import extract_repair_location


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("客厅电灯不亮了", "客厅"),
        ("主卧室的插座没电", "主卧"),
        ("洗手间的马桶漏水", "卫生间"),
        ("厕所堵了", "卫生间"),
        ("生活阳台的水管破了", "生活阳台"),
        ("厨房水槽下面在滴水", "厨房水槽下面"),
        ("次卧窗边墙皮破损", "次卧窗边"),
        ("入户门口的灯坏了", "入户门口"),
        ("3栋2单元楼道灯闪烁", "3栋2单元楼道"),
        ("我家插座坏了", None),
    ],
)
def test_extract_repair_location(text, expected):
    assert extract_repair_location(text) == expected


def test_extract_repair_location_can_prefer_the_corrected_location():
    assert extract_repair_location("不是客厅，改成厨房", prefer_last=True) == "厨房"
    assert extract_repair_location("不是厨房水槽下面，改成卫生间", prefer_last=True) == "卫生间"
    assert extract_repair_location("改成生活阳台", prefer_last=True) == "生活阳台"
    assert extract_repair_location("改成地下车库", prefer_last=True) == "地下车库"
