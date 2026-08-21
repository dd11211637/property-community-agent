#!/usr/bin/env python
"""CI gate: PostgreSQL-backed suite must run with zero skips.

Reads a pytest JUnit XML report and fails the build if any test was skipped
or if no tests were executed. This enforces that the PostgreSQL concurrency
suite (and all ``@pytest.mark.postgres`` tests) actually ran in CI instead of
being silently skipped when ``TEST_POSTGRES_URL`` is misconfigured.

Usage:
    python scripts/check_postgres_no_skip.py [junit_xml_path]

Exit code 0 = pass, 1 = at least one skip or zero tests run.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_XML = "pytest-postgres.xml"


def _read_counts(xml_path: Path) -> tuple[int, int, int]:
    """Return (tests, skipped, errors) from the first testsuite element."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # pytest emits <testsuites><testsuite .../>...</testsuites> or a bare <testsuite>.
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        raise ValueError(f"no <testsuite> element found in {xml_path}")
    tests = int(suite.get("tests", "0"))
    skipped = int(suite.get("skipped", "0"))
    errors = int(suite.get("errors", "0"))
    return tests, skipped, errors


def main(argv: list[str]) -> int:
    xml_path = Path(argv[1] if len(argv) > 1 else DEFAULT_XML)
    if not xml_path.exists():
        print(f"[check_postgres_no_skip] ERROR: JUnit report not found: {xml_path}")
        return 1

    try:
        tests, skipped, errors = _read_counts(xml_path)
    except Exception as exc:  # noqa: BLE001 - surface any parse failure as CI failure
        print(f"[check_postgres_no_skip] ERROR: failed to parse {xml_path}: {exc}")
        return 1

    print(f"[check_postgres_no_skip] tests={tests} skipped={skipped} errors={errors} ({xml_path})")

    if tests == 0:
        print(
            "[check_postgres_no_skip] FAIL: PostgreSQL suite executed 0 tests "
            "(TEST_POSTGRES_URL may be unset or the marker selected nothing)."
        )
        return 1

    if skipped > 0:
        print(
            f"[check_postgres_no_skip] FAIL: {skipped} PostgreSQL test(s) were SKIPPED. "
            "CI must run the full concurrency suite with no skips."
        )
        return 1

    print("[check_postgres_no_skip] PASS: PostgreSQL suite ran with zero skips.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
