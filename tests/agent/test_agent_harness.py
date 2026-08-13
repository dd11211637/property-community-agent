from pathlib import Path

from testing.agent_harness import load_cases, run_case


def test_controlled_read_harness_dataset_passes():
    cases = load_cases(Path("tests/agent/data/controlled_read_cases.json"))
    results = [run_case(case) for case in cases]

    assert len(results) == 7
    assert all(result.passed for result in results), results
