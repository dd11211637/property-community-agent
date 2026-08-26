"""Run a bounded pytest manifest and return aggregate, privacy-safe evidence."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def run_pytest_manifest(
    root: Path,
    targets: list[str],
    *,
    extra_args: tuple[str, ...] = (),
) -> tuple[dict[str, int], tuple[str, ...], bool]:
    with tempfile.TemporaryDirectory(prefix="pr7b-pytest-") as temp:
        report = Path(temp) / "report.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *targets,
            *extra_args,
            f"--junitxml={report}",
        ]
        completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
        if not report.exists():
            return (
                {"tests": 0, "failures": 1, "errors": 0, "skipped": 0},
                ("pytest did not produce JUnit evidence",),
                False,
            )
        suite = ET.parse(report).getroot()
        suites = [suite] if suite.tag == "testsuite" else list(suite.findall("testsuite"))
        counts = {
            key: sum(int(float(item.attrib.get(key, 0))) for item in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        limitations = () if completed.returncode == 0 else ("one or more manifest tests failed",)
        return counts, limitations, completed.returncode == 0
