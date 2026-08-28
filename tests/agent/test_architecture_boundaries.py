"""Final Agent-refactor dependency-direction contracts."""

import ast
from pathlib import Path

from property_agent.agent.infrastructure.models import AgentActionApprovalModel as AgentExport
from property_agent.agent.infrastructure.run_lease import Lease as AgentLeaseExport
from property_agent.agent.infrastructure.run_lease import (
    StaleAgentRunError as AgentStaleRunExport,
)
from property_agent.platform.infrastructure.agent_fence import Lease as PlatformLease
from property_agent.platform.infrastructure.agent_fence import (
    StaleAgentRunError as PlatformStaleRun,
)
from property_agent.platform.infrastructure.approval_models import (
    AgentActionApprovalModel as PlatformApproval,
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_platform_application_does_not_depend_on_agent() -> None:
    application_root = Path("src/property_agent/platform/application")
    violations = {
        str(path): sorted(
            module
            for module in _imported_modules(path)
            if module == "property_agent.agent" or module.startswith("property_agent.agent.")
        )
        for path in application_root.glob("*.py")
    }
    assert not {path: modules for path, modules in violations.items() if modules}


def test_legacy_agent_imports_reexport_platform_authorities() -> None:
    assert AgentExport is PlatformApproval
    assert AgentLeaseExport is PlatformLease
    assert AgentStaleRunExport is PlatformStaleRun
