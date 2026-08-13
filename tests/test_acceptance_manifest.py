import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "testing" / "scenarios" / "acceptance_matrix.json"


def test_acceptance_manifest_covers_every_prd_scenario_with_resolvable_evidence() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]
    expected_ids = {
        *(f"F-{number:02d}" for number in range(1, 7)),
        *(f"A-{number:02d}" for number in range(1, 5)),
        *(f"S-{number:02d}" for number in range(1, 5)),
        *(f"R-{number:02d}" for number in range(1, 5)),
    }

    ids = [scenario["id"] for scenario in scenarios]
    assert len(ids) == 18
    assert len(ids) == len(set(ids))
    assert set(ids) == expected_ids

    for scenario in scenarios:
        assert scenario["title"].strip()
        assert scenario["evidence"]
        for evidence in scenario["evidence"]:
            relative_path, separator, symbol = evidence.partition("::")
            assert separator and symbol.strip(), f"无效证据格式: {evidence}"
            source_path = ROOT / relative_path
            assert source_path.is_file(), f"证据文件不存在: {relative_path}"
            assert symbol in source_path.read_text(encoding="utf-8"), f"证据符号不存在: {evidence}"
