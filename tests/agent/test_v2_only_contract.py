from pathlib import Path

import pytest

from property_agent.agent.runtime_version import AgentRuntimeVersion
from property_agent.config import Settings


def test_only_v2_runtime_identity_is_accepted() -> None:
    assert AgentRuntimeVersion.from_str(None) is AgentRuntimeVersion.V2
    assert AgentRuntimeVersion.from_str("v2") is AgentRuntimeVersion.V2
    with pytest.raises(ValueError, match="retired"):
        AgentRuntimeVersion.from_str("v1")


def test_production_requires_real_model_key() -> None:
    settings = Settings(
        env="production",
        jwt_secret="x" * 40,
        database_url="postgresql+psycopg://app:secret@db/property_agent",
        deepseek_api_key="",
        otel_enabled=False,
    )
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        settings.validate_runtime_security()


def test_production_selector_paths_have_no_v1_dispatch() -> None:
    root = Path(__file__).parents[2] / "src" / "property_agent"
    paths = (
        root / "agent" / "application" / "composition.py",
        root / "agent" / "application" / "facade.py",
        root / "agent" / "application" / "graph_engine.py",
        root / "agent" / "application" / "runner.py",
        root / "agent" / "runtime_version.py",
        root / "config.py",
    )
    forbidden = ("LegacyGraphEngine", '"v1"', "'v1'", "fallback_runtime: v1")
    findings = [
        f"{path.name}:{token}"
        for path in paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]
    assert findings == []
