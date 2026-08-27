"""PR7-C Gap 1 (activation audit) + Gap 2 (freshness-bounded readiness) patches.

These tests prove the two production-control gaps called out in independent
review are closed without weakening the existing canary invariants.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from property_agent.agent.adapters.api.schemas import SendMessageRequest
from property_agent.agent.application.composition import build_rollout_control_from_settings
from property_agent.agent.observability import AgentObservability
from property_agent.agent.runtime_rollout import (
    EligibilityReason,
    RolloutActivationError,
    RolloutActivationManifest,
    RolloutActivationManifestStatus,
    RolloutAuditEvent,
    RolloutChangeReason,
    RolloutConfig,
    RolloutControl,
    RolloutReleaseIdentity,
    activate_rollout_control,
    load_rollout_activation_manifest,
    parse_rollout_activation_manifest,
)
from property_agent.agent.runtime_version import (
    DEFAULT_READINESS_TTL_SECONDS,
    AgentRuntimeVersion,
    RuntimeEligibility,
    RuntimeSelectionPolicy,
)

SALT = b"a" * 32
COMMUNITY_ID = UUID("00000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")


def _config(basis_points: int, *, version: str = "cfg-v1", salt: bytes = SALT):
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


def _identity(
    *,
    bps: int = 500,
    release_sha: str = "deploy-sha",
    config_version: str = "cfg-v1",
    approver: str = "ops:42",
):
    return RolloutReleaseIdentity(
        release_sha=release_sha,
        rollout_config_version=config_version,
        rollout_basis_points=bps,
        salt_version="salt-v1",
        eligibility_policy_version="pr7c-eligibility-v1",
        approved_fallback_runtime="v1",
        model_approval_id="model:deepseek-v4",
        prompt_contract_version="pc-v2",
        approver_reference=approver,
        approved_at="2026-08-27T00:00:00+00:00",
    )


def _approved_manifest(identity: RolloutReleaseIdentity) -> RolloutActivationManifest:
    return RolloutActivationManifest(
        identity=identity, status=RolloutActivationManifestStatus.APPROVED
    )


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# ═══════════════════════════════════════════════════════════════════════════
# Gap 1 — Production rollout activation audit (fail closed)
# ═══════════════════════════════════════════════════════════════════════════


def test_zero_rollout_activates_without_manifest() -> None:
    control = activate_rollout_control(RolloutConfig(), release_sha=None, manifest=None)
    assert control.config.basis_points == 0


def test_nonzero_without_manifest_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="APPROVED"):
        activate_rollout_control(_config(500), release_sha=None, manifest=None)


def test_nonzero_with_pending_manifest_fails_closed() -> None:
    manifest = RolloutActivationManifest(
        identity=_identity(), status=RolloutActivationManifestStatus.PENDING
    )
    with pytest.raises(RolloutActivationError):
        activate_rollout_control(_config(500), release_sha="deploy-sha", manifest=manifest)


def test_nonzero_with_revoked_manifest_fails_closed() -> None:
    manifest = RolloutActivationManifest(
        identity=_identity(), status=RolloutActivationManifestStatus.REVOKED
    )
    with pytest.raises(RolloutActivationError):
        activate_rollout_control(_config(500), release_sha="deploy-sha", manifest=manifest)


def test_nonzero_activation_requires_approved_manifest_and_emits_audit() -> None:
    events: list[RolloutAuditEvent] = []
    control = activate_rollout_control(
        _config(500),
        release_sha="deploy-sha",
        manifest=_approved_manifest(_identity(bps=500, release_sha="deploy-sha")),
        audit_sink=events.append,
    )
    assert control.config.basis_points == 500
    assert len(events) == 1
    event = events[0]
    assert event.old_basis_points == 0
    assert event.new_basis_points == 500
    assert event.new_config_version == "cfg-v1"
    assert event.reason is RolloutChangeReason.APPROVED_PROMOTION
    assert event.release_sha == "deploy-sha"
    assert event.operator_reference == "ops:42"
    assert not hasattr(event, "secret_salt")


def test_activation_audit_carries_exact_release_sha_and_bounded_approver() -> None:
    events: list[RolloutAuditEvent] = []
    activate_rollout_control(
        _config(500),
        release_sha="abc123def456",
        manifest=_approved_manifest(
            _identity(bps=500, release_sha="abc123def456", approver="approver:ops-77")
        ),
        audit_sink=events.append,
    )
    assert events[0].release_sha == "abc123def456"
    assert events[0].operator_reference == "approver:ops-77"
    # Secret salt must never be part of the evidence.
    assert SALT.decode() not in repr(events[0])
    assert "secret" not in repr(events[0])


def test_activation_release_sha_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="release_sha"):
        activate_rollout_control(
            _config(500),
            release_sha="deploy-sha",
            manifest=_approved_manifest(_identity(bps=500, release_sha="other-sha")),
        )


def test_activation_config_version_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="config_version"):
        activate_rollout_control(
            _config(500, version="cfg-v2"),
            release_sha="deploy-sha",
            manifest=_approved_manifest(_identity(bps=500, config_version="cfg-v1")),
        )


def test_activation_basis_points_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="basis_points"):
        activate_rollout_control(
            _config(500),
            release_sha="deploy-sha",
            manifest=_approved_manifest(_identity(bps=600, release_sha="deploy-sha")),
        )


def test_rollback_records_required_evidence_and_changes_future_only() -> None:
    events: list[RolloutAuditEvent] = []
    control = activate_rollout_control(
        _config(500),
        release_sha="deploy-sha",
        manifest=_approved_manifest(_identity(bps=500, release_sha="deploy-sha", approver="ops:1")),
        audit_sink=events.append,
    )
    rollback = control.rollback_to_zero(
        config_version="cfg-v3",
        reason=RolloutChangeReason.INCIDENT_ROLLBACK,
        operator_reference="incident:7",
    )
    assert control.config.basis_points == 0
    assert rollback.old_basis_points == 500
    assert rollback.new_basis_points == 0
    assert rollback.release_sha == "deploy-sha"
    assert rollback.operator_reference == "incident:7"
    assert rollback.reason is RolloutChangeReason.INCIDENT_ROLLBACK
    assert not hasattr(rollback, "secret_salt")


def test_rollout_increase_still_requires_explicit_approval_after_activation() -> None:
    control = activate_rollout_control(
        _config(500),
        release_sha="deploy-sha",
        manifest=_approved_manifest(_identity(bps=500, release_sha="deploy-sha")),
    )
    with pytest.raises(ValueError, match="explicit approval"):
        control.apply(
            _config(1000, version="cfg-v2"),
            reason=RolloutChangeReason.APPROVED_PROMOTION,
            operator_reference="ops:2",
        )
    control.apply(
        _config(1000, version="cfg-v2"),
        reason=RolloutChangeReason.APPROVED_PROMOTION,
        operator_reference="ops:2",
        promotion_approved=True,
    )
    assert control.config.basis_points == 1000


def test_load_manifest_missing_and_invalid_fail_closed() -> None:
    assert load_rollout_activation_manifest("config/does-not-exist.json") is None
    # Corrupt JSON still returns None (no exception leaks to startup).
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert load_rollout_activation_manifest(str(path)) is None


def test_parse_manifest_roundtrip_and_secret_salt_absent() -> None:
    data = {
        "identity": {
            "release_sha": "deploy-sha",
            "rollout_config_version": "cfg-v1",
            "rollout_basis_points": 500,
            "salt_version": "salt-v1",
            "eligibility_policy_version": "pr7c-eligibility-v1",
            "approved_fallback_runtime": "v1",
            "model_approval_id": "model:deepseek-v4",
            "prompt_contract_version": "pc-v2",
            "approver_reference": "ops:42",
            "approved_at": "2026-08-27T00:00:00+00:00",
        },
        "status": "approved",
    }
    manifest = parse_rollout_activation_manifest(data)
    assert manifest.status is RolloutActivationManifestStatus.APPROVED
    assert manifest.identity.rollout_basis_points == 500
    assert manifest.identity.release_sha == "deploy-sha"
    # The canonical identity must never carry the secret salt.
    assert "secret_salt" not in manifest.identity.__dataclass_fields__


# ═══════════════════════════════════════════════════════════════════════════
# Gap 1 — Real production composition activation path
# ═══════════════════════════════════════════════════════════════════════════


class _FakeSettings:
    agent_v2_new_conversation_rollout_basis_points: int = 0
    agent_v2_rollout_salt: str = ""
    agent_v2_rollout_salt_version: str = "unconfigured"
    agent_v2_rollout_config_version: str = "pr7c-default-v1"
    agent_v2_eligibility_policy_version: str = "pr7c-eligibility-v1"
    agent_v2_new_conversation_fallback_runtime: str = "v1"
    release_sha: str = ""


def _settings(*, basis_points: int, release_sha: str = "") -> _FakeSettings:
    settings = _FakeSettings()
    settings.agent_v2_new_conversation_rollout_basis_points = basis_points
    settings.agent_v2_rollout_salt = "x" * 32
    settings.agent_v2_rollout_salt_version = "salt-v1"
    settings.agent_v2_rollout_config_version = "cfg-v1"
    settings.release_sha = release_sha
    return settings


def test_composition_zero_rollout_activates_without_manifest() -> None:
    control = build_rollout_control_from_settings(_FakeSettings(), manifest=None, audit_sink=None)
    assert control.config.basis_points == 0


def test_composition_nonzero_without_manifest_fails_closed() -> None:
    with pytest.raises(RolloutActivationError):
        build_rollout_control_from_settings(
            _settings(basis_points=500, release_sha="deploy-sha"),
            manifest=None,
            audit_sink=None,
        )


def test_composition_environment_restart_zero_to_five_percent_cannot_skip_audit() -> None:
    # A deployment that flips config 0 -> 500% without an approved manifest must
    # NOT silently start serving v2; the activation boundary is enforced.
    with pytest.raises(RolloutActivationError):
        build_rollout_control_from_settings(
            _settings(basis_points=500, release_sha="deploy-sha"),
            manifest=None,
            audit_sink=None,
        )


def test_composition_nonzero_with_approved_manifest_activates_and_audits() -> None:
    events: list[RolloutAuditEvent] = []
    control = build_rollout_control_from_settings(
        _settings(basis_points=500, release_sha="deploy-sha"),
        manifest=_approved_manifest(_identity(bps=500, release_sha="deploy-sha", approver="ops:9")),
        audit_sink=events.append,
    )
    assert control.config.basis_points == 500
    assert len(events) == 1
    assert events[0].release_sha == "deploy-sha"
    assert events[0].operator_reference == "ops:9"
    assert events[0].new_basis_points == 500


def test_composition_release_sha_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="release_sha"):
        build_rollout_control_from_settings(
            _settings(basis_points=500, release_sha="deploy-sha"),
            manifest=_approved_manifest(_identity(bps=500, release_sha="wrong-sha")),
            audit_sink=None,
        )


def test_composition_audit_evidence_omits_secret_salt() -> None:
    events: list[RolloutAuditEvent] = []
    build_rollout_control_from_settings(
        _settings(basis_points=500, release_sha="deploy-sha"),
        manifest=_approved_manifest(_identity(bps=500, release_sha="deploy-sha")),
        audit_sink=events.append,
    )
    assert SALT.decode() not in repr(events[0])
    observability = AgentObservability.in_memory()
    observability.observe_rollout_audit_event(events[0])
    for point in observability.points:
        assert SALT.decode() not in repr(point)


# ═══════════════════════════════════════════════════════════════════════════
# Gap 2 — Freshness-bounded readiness snapshot (6 deterministic cases)
# ═══════════════════════════════════════════════════════════════════════════


def _policy_with_readiness(*, clock: _Clock, basis_points: int = 10_000):
    return RuntimeSelectionPolicy(
        control=RolloutControl(_config(basis_points)),
        eligibility=_eligible(),
        clock=clock,
        readiness_ttl_seconds=DEFAULT_READINESS_TTL_SECONDS,
    )


def _inputs():
    return {
        "community_id": COMMUNITY_ID,
        "actor_id": ACTOR_ID,
        "conversation_id": "readiness-conversation",
    }


def test_fresh_healthy_snapshot_allows_v2_assignment() -> None:
    clock = _Clock(1000.0)
    policy = _policy_with_readiness(clock=clock)
    policy.observe_accepted_head(available=True)
    decision = policy.decide_new(**_inputs())
    assert decision.runtime_version == "v2"


def test_missing_snapshot_falls_back_to_v1() -> None:
    clock = _Clock(1000.0)
    policy = _policy_with_readiness(clock=clock)
    # No observe() call -> missing snapshot.
    decision = policy.decide_new(**_inputs())
    assert decision.runtime_version == "v1"
    assert decision.eligibility_reason is EligibilityReason.ACCEPTED_HEAD_UNAVAILABLE


def test_expired_healthy_snapshot_falls_back_to_v1() -> None:
    clock = _Clock(1000.0)
    policy = _policy_with_readiness(clock=clock)
    policy.observe_accepted_head(available=True)
    # Advance past the TTL (default 60s).
    clock.now = 1000.0 + DEFAULT_READINESS_TTL_SECONDS + 1
    decision = policy.decide_new(**_inputs())
    assert decision.runtime_version == "v1"
    assert decision.eligibility_reason is EligibilityReason.ACCEPTED_HEAD_UNAVAILABLE


def test_fresh_unhealthy_snapshot_falls_back_to_v1() -> None:
    clock = _Clock(1000.0)
    policy = _policy_with_readiness(clock=clock)
    policy.observe_accepted_head(available=False)
    decision = policy.decide_new(**_inputs())
    assert decision.runtime_version == "v1"
    assert decision.eligibility_reason is EligibilityReason.ACCEPTED_HEAD_UNAVAILABLE


def test_recovery_creates_fresh_healthy_snapshot() -> None:
    clock = _Clock(1000.0)
    policy = _policy_with_readiness(clock=clock)
    policy.observe_accepted_head(available=False)
    # Recovery at a fresh time with a healthy probe.
    clock.now = 2000.0
    policy.observe_accepted_head(available=True)
    decision = policy.decide_new(**_inputs())
    assert decision.runtime_version == "v2"


def test_readiness_freshness_never_changes_persisted_pin() -> None:
    policy = RuntimeSelectionPolicy()
    assert policy.select_for("v2") is AgentRuntimeVersion.V2
    assert policy.select_for("v1") is AgentRuntimeVersion.V1
    # A stale/unhealthy snapshot must not flip an already-pinned v2 to v1.
    clock = _Clock(1000.0)
    failing = _policy_with_readiness(clock=clock)
    failing.observe_accepted_head(available=False)
    assert failing.select_for("v2") is AgentRuntimeVersion.V2
    assert failing.select_for("v1") is AgentRuntimeVersion.V1


# ═══════════════════════════════════════════════════════════════════════════
# Preserved invariants
# ═══════════════════════════════════════════════════════════════════════════


def test_public_default_rollout_remains_zero_basis_points() -> None:
    assert RolloutConfig().basis_points == 0
    assert RuntimeSelectionPolicy().readiness()["rollout_basis_points"] == 0
    assert RuntimeSelectionPolicy().readiness()["state"] == "OPTIONAL_ZERO"


def test_public_request_schema_has_no_runtime_selector() -> None:
    assert "runtime_version" not in SendMessageRequest.model_fields


def test_pinned_waiting_confirm_and_active_are_immutable() -> None:
    policy = RuntimeSelectionPolicy()
    for runtime in ("v1", "v2"):
        for _persisted in ("ACTIVE", "WAITING_CONFIRM", "HANDOVER"):
            # select_for ignores readiness; persistence owns the runtime.
            assert policy.select_for(runtime).value == runtime
