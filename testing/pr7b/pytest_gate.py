"""Run a bounded pytest manifest and return aggregate, privacy-safe evidence."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
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


def run_pytest_targets(
    root: Path,
    targets: list[str],
    *,
    extra_args: tuple[str, ...] = (),
) -> tuple[dict[str, str], dict[str, int], tuple[str, ...]]:
    """Run every unique exact target separately so evidence cannot bleed across cases."""
    results: dict[str, str] = {}
    totals: Counter[str] = Counter()
    limitations: list[str] = []
    for target in sorted(set(targets)):
        counts, target_limitations, passed = run_pytest_manifest(
            root, [target], extra_args=extra_args
        )
        totals.update(counts)
        if counts["tests"] == 0 or (
            counts["skipped"] == counts["tests"] and not counts["failures"] and not counts["errors"]
        ):
            status = "NOT_RUN"
        elif passed and counts["skipped"] == 0:
            status = "PASS"
        else:
            status = "FAIL"
        results[target] = status
        limitations.extend(f"{target}: {item}" for item in target_limitations)
    return results, dict(totals), tuple(limitations)
