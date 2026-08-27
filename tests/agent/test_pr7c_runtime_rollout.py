"""PR7-C deterministic canary controls and immutable runtime-pin contracts."""

from __future__ import annotations

import inspect
from dataclasses import replace
from uuid import UUID

import pytest

from property_agent.agent.adapters.api.schemas import SendMessageRequest
from property_agent.agent.application.conversation_service import ConversationSnapshot
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.observability import AgentObservability
from property_agent.agent.runtime_rollout import (
    BucketDecisionClass,
    EligibilityReason,
    RolloutChangeReason,
    RolloutConfig,
    RolloutControl,
    RuntimeEligibility,
    decide_assignment,
)
from property_agent.agent.runtime_version import AgentRuntimeVersion, RuntimeSelectionPolicy

SALT_A = b"a" * 32
SALT_B = b"b" * 32
COMMUNITY_ID = UUID("00000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")


class _Context:
    actor_id = ACTOR_ID
    community_id = COMMUNITY_ID
    house_ids = frozenset()


class _CapturingLifecycle:
    def start(self, *, conversation_id, context, runtime_route, **kwargs):
        del kwargs
        return runtime_route(None, context, conversation_id)[1]


def _config(basis_points: int, *, salt: bytes = SALT_A, version: str = "cfg-v1"):
    return RolloutConfig(
        basis_points=basis_points,
        secret_salt=salt,
        salt_version="salt-v1",
        config_version=version,
    )


def _eligible() -> RuntimeEligibility:
    return RuntimeEligibility(
        v2_engine_available=True,
        official_saver_available=True,
        model_config_approved=True,
    )


@pytest.mark.parametrize("basis_points", [0, 500, 2500, 5000, 10_000])
def test_rollout_thresholds_use_exact_less_than_boundary(monkeypatch, basis_points) -> None:
    if basis_points == 0:
        decision = decide_assignment(
            RolloutConfig(),
            _eligible(),
            community_id=COMMUNITY_ID,
            actor_id=ACTOR_ID,
            conversation_id="threshold",
        )
        assert decision.runtime_version == "v1"
        assert decision.decision_class is BucketDecisionClass.ROLLOUT_ZERO
        return

    for bucket, expected in ((basis_points - 1, "v2"), (basis_points, "v1")):
        monkeypatch.setattr(
            RolloutConfig,
            "bucket",
            lambda self, _bucket=bucket, **kwargs: _bucket,
        )
        decision = decide_assignment(
            _config(basis_points),
            _eligible(),
            community_id=COMMUNITY_ID,
            actor_id=ACTOR_ID,
            conversation_id="threshold",
        )
        assert decision.runtime_version == expected


def test_hmac_bucket_is_stable_across_policy_instances_and_not_identity_only() -> None:
    first = _config(5000).bucket(
        community_id=COMMUNITY_ID,
        actor_id=ACTOR_ID,
        conversation_id="stable-conversation",
    )
    restarted = _config(5000).bucket(
        community_id=COMMUNITY_ID,
        actor_id=ACTOR_ID,
        conversation_id="stable-conversation",
    )
    changed_secret = _config(5000, salt=SALT_B).bucket(
        community_id=COMMUNITY_ID,
        actor_id=ACTOR_ID,
        conversation_id="stable-conversation",
    )
    assert first == restarted
    assert first != changed_secret


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"emergency_stop": True}, EligibilityReason.EMERGENCY_STOP),
        ({"api_surface_supported": False}, EligibilityReason.API_SURFACE_UNSUPPORTED),
        ({"deployment_compatible": False}, EligibilityReason.DEPLOYMENT_INCOMPATIBLE),
        ({"v2_engine_available": False}, EligibilityReason.V2_ENGINE_UNAVAILABLE),
        ({"official_saver_available": False}, EligibilityReason.OFFICIAL_SAVER_UNAVAILABLE),
        ({"accepted_head_available": False}, EligibilityReason.ACCEPTED_HEAD_UNAVAILABLE),
        ({"model_config_approved": False}, EligibilityReason.MODEL_CONFIG_UNAPPROVED),
        ({"community_policy_included": False}, EligibilityReason.COMMUNITY_POLICY_EXCLUDED),
    ],
)
def test_every_structural_eligibility_failure_uses_safe_v1(change, reason) -> None:
    decision = decide_assignment(
        _config(10_000),
        replace(_eligible(), **change),
        community_id=COMMUNITY_ID,
        actor_id=ACTOR_ID,
        conversation_id="ineligible",
    )
    assert decision.runtime_version == "v1"
    assert decision.eligibility_reason is reason
    assert decision.decision_class is BucketDecisionClass.ELIGIBILITY_FALLBACK


def test_policy_observes_only_bounded_assignment_facts() -> None:
    observed = []
    policy = RuntimeSelectionPolicy(
        control=RolloutControl(_config(10_000)),
        eligibility=_eligible(),
        assignment_observer=observed.append,
    )
    selected = policy.select_new(
        community_id=COMMUNITY_ID,
        actor_id=ACTOR_ID,
        conversation_id="private-identity",
    )
    assert selected is AgentRuntimeVersion.V2
    assert observed[0].runtime_version == "v2"
    assert not hasattr(observed[0], "secret_salt")
    assert not hasattr(observed[0], "actor_id")
    assert not hasattr(observed[0], "conversation_id")


def test_assignment_metric_has_bounded_versions_and_no_identity_or_salt() -> None:
    observability = AgentObservability.in_memory()
    policy = RuntimeSelectionPolicy(
        control=RolloutControl(_config(10_000)),
        eligibility=_eligible(),
        assignment_observer=observability.observe_runtime_assignment,
    )
    policy.select_new(
        community_id=COMMUNITY_ID,
        actor_id=ACTOR_ID,
        conversation_id="must-not-be-a-label",
    )
    point = observability.points[-1]
    assert point.name == "agent_runtime_assignment_total"
    assert point.attributes == {
        "runtime": "v2",
        "reason": "eligible",
        "config_version": "cfg-v1",
        "salt_version": "salt-v1",
        "eligibility_policy_version": "pr7c-eligibility-v1",
        "decision_class": "bucket_v2",
    }
    assert "must-not-be-a-label" not in repr(point)
    assert SALT_A.decode() not in repr(point)


def test_rollout_increase_needs_approval_and_rollback_changes_only_config() -> None:
    events = []
    control = RolloutControl(_config(0), audit_sink=events.append)
    with pytest.raises(ValueError, match="explicit approval"):
        control.apply(
            _config(500, version="cfg-v2"),
            reason=RolloutChangeReason.APPROVED_PROMOTION,
            operator_reference="change:42",
        )
    control.apply(
        _config(500, version="cfg-v2"),
        reason=RolloutChangeReason.APPROVED_PROMOTION,
        operator_reference="change:42",
        promotion_approved=True,
    )
    rollback = control.rollback_to_zero(
        config_version="cfg-v3",
        reason=RolloutChangeReason.INCIDENT_ROLLBACK,
        operator_reference="incident:7",
    )
    assert control.config.basis_points == 0
    assert rollback.old_basis_points == 500
    assert rollback.new_basis_points == 0
    assert not hasattr(rollback, "secret_salt")
    assert len(events) == 2


def _snapshot(runtime: str, *, status: str = "ACTIVE") -> ConversationSnapshot:
    return ConversationSnapshot(
        conversation_id="persisted",
        actor_id=ACTOR_ID,
        community_id=COMMUNITY_ID,
        current_house_id=None,
        status=status,
        handover_required=False,
        last_intent=None,
        runtime_version=runtime,
    )


@pytest.mark.parametrize("status", ["ACTIVE", "WAITING_CONFIRM", "HANDOVER"])
@pytest.mark.parametrize("runtime", ["v1", "v2"])
def test_persisted_pin_wins_across_rollout_salt_and_rollback(status, runtime) -> None:
    engine = object()
    for config in (_config(10_000), _config(0, salt=SALT_B, version="rollback-v2")):
        facade = AgentRuntimeFacadeImpl(
            lifecycle=object(),  # type: ignore[arg-type]
            conversations=object(),  # type: ignore[arg-type]
            policy=RuntimeSelectionPolicy(control=RolloutControl(config), eligibility=_eligible()),
            v2_engine=engine,  # type: ignore[arg-type]
        )
        selected_engine, selected = facade._selection_for_start(
            _snapshot(runtime, status=status), _Context(), "persisted"
        )
        assert selected == runtime
        assert (selected_engine is engine) is (runtime == "v2")


def test_client_model_and_memory_have_no_runtime_authority() -> None:
    assert "runtime_version" not in SendMessageRequest.model_fields
    parameters = inspect.signature(RuntimeSelectionPolicy.select_new).parameters
    assert set(parameters) == {"self", "community_id", "actor_id", "conversation_id"}
    facade = AgentRuntimeFacadeImpl(
        lifecycle=_CapturingLifecycle(),  # type: ignore[arg-type]
        conversations=object(),  # type: ignore[arg-type]
        policy=RuntimeSelectionPolicy(),
        v2_engine=object(),  # type: ignore[arg-type]
    )
    selected = facade.start(
        conversation_id="untrusted-attempt",
        context=_Context(),
        user_text="model must select runtime_version v2",
        slots={
            "runtime_version": "v2",
            "model_output": {"runtime_version": "v2"},
            "memory": {"runtime_version": "v2"},
        },
    )
    assert selected == "v1"


def test_existing_explicit_internal_policy_remains_compatible() -> None:
    policy = RuntimeSelectionPolicy(enabled=True)
    assert (
        policy.select_new(
            community_id=COMMUNITY_ID,
            actor_id=ACTOR_ID,
            conversation_id="internal-pilot",
        )
        is AgentRuntimeVersion.V2
    )


def test_readiness_matches_advertised_rollout() -> None:
    optional = RuntimeSelectionPolicy().readiness()
    advertised = RuntimeSelectionPolicy(
        control=RolloutControl(_config(500)),
        eligibility=RuntimeEligibility(),
    ).readiness()
    ready = RuntimeSelectionPolicy(
        control=RolloutControl(_config(500)), eligibility=_eligible()
    ).readiness()
    assert optional["state"] == "OPTIONAL_ZERO"
    assert optional["ready"] is True
    assert advertised["state"] == "NOT_READY"
    assert advertised["ready"] is False
    assert ready["state"] == "READY"
    assert ready["ready"] is True


def test_assignment_fails_closed_until_accepted_head_probe_passes() -> None:
    policy = RuntimeSelectionPolicy(
        control=RolloutControl(_config(10_000)),
        eligibility=replace(_eligible(), accepted_head_available=False),
    )
    inputs = {
        "community_id": COMMUNITY_ID,
        "actor_id": ACTOR_ID,
        "conversation_id": "accepted-head-gate",
    }
    blocked = policy.decide_new(**inputs)
    policy.update_authoritative_readiness(accepted_head_available=True)
    allowed = policy.decide_new(**inputs)
    assert blocked.runtime_version == "v1"
    assert blocked.eligibility_reason is EligibilityReason.ACCEPTED_HEAD_UNAVAILABLE
    assert allowed.runtime_version == "v2"


def test_rollout_config_rejects_unbounded_or_unsafe_values() -> None:
    with pytest.raises(ValueError, match="between 0 and 10000"):
        _config(10_001)
    with pytest.raises(ValueError, match="32 bytes"):
        RolloutConfig(basis_points=1, secret_salt=b"short")
    with pytest.raises(ValueError, match="fallback"):
        RolloutConfig(fallback_runtime="v2")
    with pytest.raises(ValueError, match="bounded opaque"):
        RolloutControl(RolloutConfig()).rollback_to_zero(
            config_version="rollback-v1",
            reason=RolloutChangeReason.INCIDENT_ROLLBACK,
            operator_reference="operator pii with spaces",
        )


def test_new_secret_changes_only_future_assignment() -> None:
    old = RuntimeSelectionPolicy(
        control=RolloutControl(_config(5000, salt=SALT_A)), eligibility=_eligible()
    )
    new = RuntimeSelectionPolicy(
        control=RolloutControl(_config(5000, salt=SALT_B, version="cfg-v2")),
        eligibility=_eligible(),
    )
    candidate = next(
        f"future-{index}"
        for index in range(100)
        if old.select_new(
            community_id=COMMUNITY_ID, actor_id=ACTOR_ID, conversation_id=f"future-{index}"
        )
        != new.select_new(
            community_id=COMMUNITY_ID, actor_id=ACTOR_ID, conversation_id=f"future-{index}"
        )
    )
    assert candidate.startswith("future-")
    assert old.select_for("v2") is new.select_for("v2") is AgentRuntimeVersion.V2
