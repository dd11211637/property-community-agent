"""Architecture guards for the billing application boundary."""

import ast
from pathlib import Path


def test_billing_application_does_not_import_framework_or_infrastructure() -> None:
    application_dir = Path("src/property_agent/billing/application")
    forbidden_prefixes = (
        "fastapi",
        "sqlalchemy",
        "property_agent.billing.infrastructure",
        "property_agent.platform.adapters",
        "property_agent.platform.infrastructure",
    )

    violations: list[str] = []
    for path in application_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(forbidden_prefixes):
                    violations.append(f"{path}:{node.lineno}: {name}")

    assert violations == []
