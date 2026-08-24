"""PR4 runtime 版本钉死与公网硬零流量契约。"""

from uuid import uuid4

import pytest

from property_agent.agent.adapters.api.schemas import SendMessageRequest
from property_agent.agent.application.conversation_service import ConversationSnapshot
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.runtime_version import AgentRuntimeVersion, RuntimeSelectionPolicy


def _snapshot(runtime_version: str) -> ConversationSnapshot:
    return ConversationSnapshot(
        conversation_id="conv-runtime-pin",
        actor_id=uuid4(),
        community_id=uuid4(),
        current_house_id=None,
        status="ACTIVE",
        handover_required=False,
        last_intent=None,
        runtime_version=runtime_version,
    )


def test_default_policy_is_public_hard_zero() -> None:
    assert RuntimeSelectionPolicy().select_new() is AgentRuntimeVersion.V1
    assert "runtime_version" not in SendMessageRequest.model_fields


def test_internal_policy_must_be_explicitly_injected() -> None:
    policy = RuntimeSelectionPolicy(enabled=True)

    assert policy.select_new() is AgentRuntimeVersion.V2
    assert policy.select_for("v1") is AgentRuntimeVersion.V1


def test_pinned_v2_never_silently_falls_back_to_legacy() -> None:
    facade = AgentRuntimeFacadeImpl(
        lifecycle=object(),  # type: ignore[arg-type]
        conversations=object(),  # type: ignore[arg-type]
        policy=RuntimeSelectionPolicy(),
        v2_engine=None,
    )

    with pytest.raises(RuntimeError, match="pinned v2 runtime is unavailable"):
        facade._engine_for_existing(_snapshot("v2"))


def test_persisted_runtime_rejects_unknown_versions() -> None:
    with pytest.raises(ValueError, match="unsupported persisted agent runtime version"):
        RuntimeSelectionPolicy().select_for("client-forged-runtime")
