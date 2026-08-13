from property_agent.platform.container import _display_part


def test_display_part_does_not_duplicate_existing_suffix():
    assert _display_part("1", "栋") == "1栋"
    assert _display_part("1栋", "栋") == "1栋"
    assert _display_part("2单元", "单元") == "2单元"
    assert _display_part("101室", "室") == "101室"
