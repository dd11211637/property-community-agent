from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_local_functional_closure_generates_all_non_external_inputs(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "local-functional-closure.json"
    environment = os.environ.copy()
    for name in (
        "DATABASE_URL",
        "JWT_SECRET",
        "PR7B_BEARER_TOKEN",
        "PR7B_HOUSE_ID",
        "PR7B_BASE_URL",
        "RELEASE_SHA",
    ):
        environment.pop(name, None)

    completed = subprocess.run(
        [sys.executable, "-m", "testing.local_functional_closure", "--output", str(output)],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert all(report["generated_inputs"].values())
    assert report["load_harness_smoke"]["status"] == "PASS"
