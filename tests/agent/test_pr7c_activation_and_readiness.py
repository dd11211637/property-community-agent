"""PR7-C Gap 1 (activation audit) + Gap 2 (freshness-bounded readiness) patches.

These tests prove the two production-control gaps called out in independent
review are closed without weakening the existing canary invariants.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from property_agent.agent.adapters.api.schemas import SendMessageRequest
from property_agent.agent.application.composition import build_rollout_control_from_settings
from property_agent.agent.model_gateway import (
    DeterministicModelGateway,
    FallbackModelGateway,
    ModelGatewayError,
)
from property_agent.agent.model_release import (
    ModelReleaseIdentity,
    actual_model_release_identity,
)
from property_agent.agent.model_release_approval import (
    BASELINE_APPROVAL_MANIFEST_VERSION,
    FALLBACK_POLICY_VERSION,
    PRIMARY_PROVIDER,
    PROVIDER_RESPONSE_CONFIG_VERSION,
    RETRY_POLICY_VERSION,
    effective_provider_config,
    primary_provider_ready,
    provider_config_fingerprint,
    verify_approval_evidence,
    verify_committed_baseline_approval,
)
from property_agent.agent.observability import AgentObservability
from property_agent.agent.runtime_rollout import (
    ROLLOUT_BASELINE_CONFIG_VERSION,
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
    compute_manifest_sha256,
    load_rollout_activation_manifest,
    parse_rollout_activation_manifest,
)
from property_agent.agent.runtime_version import (
    DEFAULT_READINESS_TTL_SECONDS,
    AgentRuntimeVersion,
    RuntimeEligibility,
    RuntimeSelectionPolicy,
)
from property_agent.config import Settings

SALT = b"a" * 32
COMMUNITY_ID = UUID("00000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")

# Exact 40-char lowercase Git commit SHAs used across activation tests.
RELEASE_SHA = "d289b13fee000000000000000000000000000000"
RELEASE_SHA_B = "f00dcafe00000000000000000000000000000000"

# Shared provider-config fingerprint used by the fixture identities (64 hex).
FINGERPRINT = "a1a1" * 16

APPROVED = RolloutActivationManifestStatus.APPROVED


def _config(
    basis_points: int,
    *,
    version: str = "cfg-v1",
    salt: bytes = SALT,
    model_approval_id: str = "real-model:approved-baseline-v1",
    prompt_contract_version: str = "semantic-planner-pr5-v1",
):
    return RolloutConfig(
        basis_points=basis_points,
        secret_salt=salt,
        salt_version="salt-v1",
        config_version=version,
        model_approval_id=model_approval_id,
        prompt_contract_version=prompt_contract_version,
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
    release_sha: str = RELEASE_SHA,
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
        model_approval_id="real-model:approved-baseline-v1",
        prompt_contract_version="semantic-planner-pr5-v1",
        approver_reference=approver,
        approved_at="2026-08-27T00:00:00+00:00",
        primary_provider="deepseek",
        model="deepseek-v4-flash",
        provider_config_version="deepseek-bounded-retry-v1",
        provider_config_fingerprint=FINGERPRINT,
        fallback_policy_version=FALLBACK_POLICY_VERSION,
        model_release_evidence_reference="real-model:approved-baseline-v1",
        previous_rollout_basis_points=0,
        previous_rollout_config_version=ROLLOUT_BASELINE_CONFIG_VERSION,
    )


def _real_actual() -> ModelReleaseIdentity:
    """A runnable certified model execution identity with verified evidence."""
    return ModelReleaseIdentity(
        primary_provider="deepseek",
        model="deepseek-v4-flash",
        provider_config_version="deepseek-bounded-retry-v1",
        provider_config_fingerprint=FINGERPRINT,
        prompt_contract_version="semantic-planner-pr5-v1",
        model_release_evidence_reference="real-model:approved-baseline-v1",
        primary_provider_ready=True,
        fallback_policy_version=FALLBACK_POLICY_VERSION,
    )


def _manifest_for(identity: RolloutReleaseIdentity, *, sha: str = "correct"):
    base = RolloutActivationManifest(identity=identity, status=APPROVED, manifest_sha256="")
    if sha == "correct":
        digest = compute_manifest_sha256(base)
    elif sha == "empty":
        digest = ""
    elif sha == "malformed":
        digest = "zzz-not-a-hex-digest"
    else:  # mismatch: valid 64-hex but not the canonical digest
        digest = "0" * 64
    return RolloutActivationManifest(identity=identity, status=APPROVED, manifest_sha256=digest)


def _approved(identity: RolloutReleaseIdentity, *, sha: str = "correct"):
    return _manifest_for(identity, sha=sha)


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
        activate_rollout_control(_config(500), release_sha=RELEASE_SHA, manifest=None)


def test_nonzero_with_pending_manifest_fails_closed() -> None:
    manifest = RolloutActivationManifest(
        identity=_identity(), status=RolloutActivationManifestStatus.PENDING
    )
    with pytest.raises(RolloutActivationError):
        activate_rollout_control(_config(500), release_sha=RELEASE_SHA, manifest=manifest)


def test_nonzero_with_revoked_manifest_fails_closed() -> None:
    manifest = RolloutActivationManifest(
        identity=_identity(), status=RolloutActivationManifestStatus.REVOKED
    )
    with pytest.raises(RolloutActivationError):
        activate_rollout_control(_config(500), release_sha=RELEASE_SHA, manifest=manifest)


def test_nonzero_activation_requires_approved_manifest_and_emits_audit() -> None:
    events: list[RolloutAuditEvent] = []
    control = activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert control.config.basis_points == 500
    assert len(events) == 1
    event = events[0]
    assert event.old_basis_points == 0
    assert event.new_basis_points == 500
    assert event.new_config_version == "cfg-v1"
    assert event.reason is RolloutChangeReason.APPROVED_PROMOTION
    assert event.release_sha == RELEASE_SHA
    assert event.approver_reference == "ops:42"
    assert not hasattr(event, "secret_salt")


def test_activation_audit_carries_exact_release_sha_and_bounded_approver() -> None:
    events: list[RolloutAuditEvent] = []
    activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA, approver="approver:ops-77")),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert events[0].release_sha == RELEASE_SHA
    assert events[0].approver_reference == "approver:ops-77"
    # Secret salt must never be part of the evidence.
    assert SALT.decode() not in repr(events[0])
    assert "secret" not in repr(events[0])


def test_activation_release_sha_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="does not match deployed"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA_B)),
        )


def test_activation_config_version_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="config_version"):
        activate_rollout_control(
            _config(500, version="cfg-v2"),
            release_sha=RELEASE_SHA,
            manifest=_approved(
                _identity(bps=500, release_sha=RELEASE_SHA, config_version="cfg-v1")
            ),
        )


def test_activation_basis_points_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="basis_points"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=600, release_sha=RELEASE_SHA)),
        )


def test_rollback_records_required_evidence_and_changes_future_only() -> None:
    events: list[RolloutAuditEvent] = []
    control = activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA, approver="ops:1")),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    rollback = control.rollback_to_zero(
        config_version="cfg-v3",
        reason=RolloutChangeReason.INCIDENT_ROLLBACK,
        approver_reference="incident:7",
        change_reference="incident:inc-7",
    )
    assert control.config.basis_points == 0
    assert rollback.old_basis_points == 500
    assert rollback.new_basis_points == 0
    assert rollback.release_sha == RELEASE_SHA
    assert rollback.approver_reference == "incident:7"
    assert rollback.change_reference == "incident:inc-7"
    assert rollback.reason is RolloutChangeReason.INCIDENT_ROLLBACK
    assert not hasattr(rollback, "secret_salt")


def test_rollout_increase_requires_new_activation_not_runtime_apply() -> None:
    # A non-zero rollout is active via an approved manifest.
    control = activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
        model_release_identity=_real_actual(),
    )
    # Runtime apply can ONLY decrease (rollback); an increase is rejected and is
    # NOT accessible through any in-process promotion flag.
    with pytest.raises(ValueError, match="new approved activation manifest"):
        control.apply(
            _config(1000, version="cfg-v2"),
            reason=RolloutChangeReason.APPROVED_PROMOTION,
            approver_reference="ops:2",
        )
    assert control.config.basis_points == 500


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
# Gap 1 — 16-case negative activation matrix (fail closed)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "cid,identity,running,sha_mode,match",
    [
        # Blocker 1 — exact 40-hex Git SHA required on BOTH sides, matched exactly.
        ("running-sha-missing", _identity(), None, "correct", "deployed release_sha"),
        ("running-sha-malformed", _identity(), "abc123", "correct", "deployed release_sha"),
        (
            "manifest-sha-missing",
            _identity(release_sha=""),
            RELEASE_SHA,
            "correct",
            "manifest release_sha",
        ),
        (
            "manifest-sha-malformed",
            _identity(release_sha="deadbeef"),
            RELEASE_SHA,
            "correct",
            "manifest release_sha",
        ),
        (
            "sha-mismatch",
            _identity(release_sha=RELEASE_SHA_B),
            RELEASE_SHA,
            "correct",
            "does not match deployed",
        ),
        # Blocker 3 — manifest_sha256 must be SHA-256 of the canonical payload.
        ("sha256-empty", _identity(), RELEASE_SHA, "empty", "manifest_sha256"),
        ("sha256-malformed", _identity(), RELEASE_SHA, "malformed", "manifest_sha256"),
        ("sha256-mismatch", _identity(), RELEASE_SHA, "mismatch", "does not match the canonical"),
        # Blocker 2 — complete identity must match the active configuration.
        (
            "config-version",
            replace(_identity(), rollout_config_version="cfg-v2"),
            RELEASE_SHA,
            "correct",
            "config_version",
        ),
        (
            "basis-points",
            replace(_identity(), rollout_basis_points=600),
            RELEASE_SHA,
            "correct",
            "basis_points",
        ),
        (
            "salt-version",
            replace(_identity(), salt_version="salt-v2"),
            RELEASE_SHA,
            "correct",
            "salt_version",
        ),
        (
            "eligibility-policy",
            replace(_identity(), eligibility_policy_version="elig-v2"),
            RELEASE_SHA,
            "correct",
            "eligibility_policy_version",
        ),
        (
            "fallback-runtime",
            replace(_identity(), approved_fallback_runtime="v2"),
            RELEASE_SHA,
            "correct",
            "approved_fallback_runtime",
        ),
        (
            "manifest-version",
            replace(_identity(), activation_manifest_version="pr7c-activation-v1"),
            RELEASE_SHA,
            "correct",
            "unsupported activation_manifest_version",
        ),
        (
            "approver-unbounded",
            replace(_identity(), approver_reference="ops with spaces"),
            RELEASE_SHA,
            "correct",
            "approver_reference",
        ),
        (
            "approved-at-invalid",
            replace(_identity(), approved_at="2026-08-27"),
            RELEASE_SHA,
            "correct",
            "approved_at",
        ),
    ],
)
def test_activation_negative_matrix(cid, identity, running, sha_mode, match) -> None:
    manifest = _manifest_for(identity, sha=sha_mode)
    with pytest.raises(RolloutActivationError, match=match):
        activate_rollout_control(
            _config(500),
            release_sha=running,
            manifest=manifest,
            model_release_identity=_real_actual(),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Blocker 1 — bind rollout approval to the certified execution contract + readiness
# ═══════════════════════════════════════════════════════════════════════════


def test_blocker1_actual_model_must_match_approved_manifest() -> None:
    # Approved-looking manifest, but the certified DeepSeek model differs.
    actual = replace(_real_actual(), model="deepseek-v4-pro")
    with pytest.raises(RolloutActivationError, match="model"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
            model_release_identity=actual,
        )


def test_blocker1_wrong_provider_config_version_rejected() -> None:
    actual = replace(_real_actual(), provider_config_version="deepseek-bounded-retry-v2")
    with pytest.raises(RolloutActivationError, match="provider_config_version"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
            model_release_identity=actual,
        )


def test_blocker1_wrong_primary_provider_rejected() -> None:
    identity = replace(_identity(), primary_provider="deterministic")
    with pytest.raises(RolloutActivationError, match="primary_provider"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=_real_actual(),
        )


def test_blocker1_wrong_fallback_policy_rejected() -> None:
    identity = replace(_identity(), fallback_policy_version="no-fallback-v1")
    with pytest.raises(RolloutActivationError, match="fallback_policy_version"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=_real_actual(),
        )


def test_blocker1_wrong_prompt_contract_rejected() -> None:
    actual = replace(_real_actual(), prompt_contract_version="semantic-planner-pr5-v2")
    with pytest.raises(RolloutActivationError, match="prompt_contract_version"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
            model_release_identity=actual,
        )


def test_blocker1_duplicate_looking_strings_without_real_evidence_rejected() -> None:
    # The manifest carries real-looking model_approval_id/model_release_evidence_reference,
    # but the certified release evidence reference is empty (PENDING
    # baseline -> no verified artifact), so no string can self-authorize.
    actual = replace(_real_actual(), model_release_evidence_reference="")
    identity = replace(
        _identity(bps=500, release_sha=RELEASE_SHA),
        model_approval_id="real-model:looks-approved",
        model_release_evidence_reference="real-model:looks-approved",
    )
    with pytest.raises(RolloutActivationError, match="PENDING"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=actual,
        )


def test_blocker1_actual_release_identity_match_proceeds() -> None:
    # Matching certified contract, current readiness, and verified evidence proceeds.
    events: list[RolloutAuditEvent] = []
    control = activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert control.config.basis_points == 500
    assert events[0].new_basis_points == 500


# ═══════════════════════════════════════════════════════════════════════════
# Model-release governance — shared approval contract + actual binding
# (Blocker A–E: PENDING fail-closed, artifact digest, evidence reference,
#  actual provider, effective provider config)
# ═══════════════════════════════════════════════════════════════════════════


def _provider_settings(
    *,
    base_url: str = "https://api.deepseek.com",
    api_key: str = "sk-test-key",
    total_timeout: float = 6.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        deepseek_base_url=base_url,
        deepseek_model="deepseek-v4-flash",
        deepseek_api_key=api_key,
        deepseek_connect_timeout_seconds=3.0,
        deepseek_read_timeout_seconds=12.0,
        deepseek_total_timeout_seconds=total_timeout,
    )


def _verified_evidence_reference() -> str:
    return "pr7b-real-model:" + "a" * 64


def test_gov_a_baseline_pending_fails_closed_via_shared_validator() -> None:
    # The committed baseline approval is PENDING with an empty artifact_sha256, so
    # the shared production validator yields no verified evidence and the certified
    # identity carries an EMPTY evidence reference (never "PENDING").
    assert verify_committed_baseline_approval() is None
    actual = actual_model_release_identity()
    assert actual.model_release_evidence_reference == ""
    # Non-zero activation fails closed on provider readiness or PENDING evidence.
    with pytest.raises(RolloutActivationError):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
            model_release_identity=actual,
        )
    # Even when provider matches, the PENDING-derived empty evidence reference must
    # fail closed — this is the governance, not a mocked "PENDING" string check.
    pending = replace(_real_actual(), model_release_evidence_reference="")
    with pytest.raises(RolloutActivationError, match="PENDING"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
            model_release_identity=pending,
        )


def test_gov_b_pending_equality_cannot_self_authorize() -> None:
    # actual / manifest all carry the literal "PENDING" and match each other, yet
    # fail closed: equality of PENDING strings is not approval evidence.
    pending_actual = replace(_real_actual(), model_release_evidence_reference="PENDING")
    identity = replace(
        _identity(bps=500, release_sha=RELEASE_SHA),
        model_approval_id="PENDING",
        model_release_evidence_reference="PENDING",
    )
    with pytest.raises(RolloutActivationError, match="PENDING"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=pending_actual,
        )


def test_gov_c_approved_without_digest_fails_closed() -> None:
    # approval_status=APPROVED but artifact_sha256="" -> NOT verified evidence.
    approval = {
        "approval_manifest_version": BASELINE_APPROVAL_MANIFEST_VERSION,
        "approval_status": "APPROVED",
        "artifact_path": "config/pr7b_real_model_approved_baseline_v1.json",
        "artifact_sha256": "",
    }
    assert verify_approval_evidence(approval, artifact_bytes=b"{}") is None


def test_gov_d_digest_mismatch_fails_closed() -> None:
    # approval_status=APPROVED but artifact_sha256 is valid-looking yet wrong.
    approval = {
        "approval_manifest_version": BASELINE_APPROVAL_MANIFEST_VERSION,
        "approval_status": "APPROVED",
        "artifact_path": "config/pr7b_real_model_approved_baseline_v1.json",
        "artifact_sha256": "0" * 64,
    }
    assert verify_approval_evidence(approval, artifact_bytes=b'{"x": 1}') is None


def test_gov_e_verified_approval_generates_evidence_reference(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    artifact = config_dir / "approved_baseline_v1.json"
    artifact.write_bytes(b'{"baseline_version": "v1"}')
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    approval = {
        "approval_manifest_version": BASELINE_APPROVAL_MANIFEST_VERSION,
        "approval_status": "APPROVED",
        "artifact_path": "config/approved_baseline_v1.json",
        "artifact_sha256": digest,
    }
    verified = verify_approval_evidence(approval, artifact_bytes=artifact.read_bytes())
    assert verified is not None
    assert verified.artifact_sha256 == digest
    assert verified.evidence_reference == f"pr7b-real-model:{digest}"
    # Same contract through the committed-manifest loader with a repo-root override.
    (config_dir / "pr7b_real_model_baseline_approval.json").write_text(
        json.dumps(approval), encoding="utf-8"
    )
    committed = verify_committed_baseline_approval(repo_root=tmp_path)
    assert committed is not None
    assert committed.evidence_reference == f"pr7b-real-model:{digest}"


def test_gov_e_resolved_artifact_must_remain_under_config(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"outside": true}')
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    approval = {
        "approval_manifest_version": BASELINE_APPROVAL_MANIFEST_VERSION,
        "approval_status": "APPROVED",
        "artifact_path": "config/link.json",
        "artifact_sha256": digest,
    }
    (config_dir / "pr7b_real_model_baseline_approval.json").write_text(
        json.dumps(approval), encoding="utf-8"
    )
    original_resolve = Path.resolve
    escaped_path = original_resolve(outside)
    linked_path = config_dir / "link.json"

    def resolve_with_escape(path, *args, **kwargs):
        if path == linked_path:
            return escaped_path
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_escape)
    assert verify_committed_baseline_approval(repo_root=tmp_path) is None


def test_gov_f_forged_evidence_reference_fails_closed() -> None:
    # The manifest's model_release_evidence_reference is hashed into the digest but
    # must ALSO be validated against the actual release: a forged value fails even
    # when the digest is recomputed correctly and model_approval_id matches.
    evidence = _verified_evidence_reference()
    actual = replace(_real_actual(), model_release_evidence_reference=evidence)
    identity = replace(
        _identity(bps=500, release_sha=RELEASE_SHA),
        model_approval_id=evidence,
        model_release_evidence_reference="forged:other-authority",
    )
    with pytest.raises(RolloutActivationError, match="model_release_evidence_reference"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=actual,
        )


def test_gov_g_model_approval_id_mismatch_fails_closed() -> None:
    evidence = _verified_evidence_reference()
    actual = replace(_real_actual(), model_release_evidence_reference=evidence)
    identity = replace(
        _identity(bps=500, release_sha=RELEASE_SHA),
        model_approval_id="different-authority:z",
        model_release_evidence_reference=evidence,
    )
    with pytest.raises(RolloutActivationError, match="model_approval_id"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=actual,
        )


def test_gov_h_unready_primary_provider_cannot_authorize_rollout() -> None:
    # No DeepSeek credential -> the certified DeepSeek primary is NOT runnable, so a
    # non-zero rollout must fail closed. The identity is NEVER mutated to "deterministic":
    # primary_provider stays the certified DeepSeek contract; readiness is a separate
    # runtime gate that activation checks independently.
    assert primary_provider_ready(_provider_settings(api_key="")) is False
    assert primary_provider_ready(_provider_settings(api_key="sk-key")) is True
    assert PRIMARY_PROVIDER == "deepseek"
    without_key = _provider_settings(api_key="")
    with_key = _provider_settings(api_key="sk-key")
    assert effective_provider_config(without_key) == effective_provider_config(with_key)
    assert provider_config_fingerprint(without_key) == provider_config_fingerprint(with_key)
    not_ready = replace(_real_actual(), primary_provider_ready=False)
    with pytest.raises(RolloutActivationError, match="primary provider"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
            model_release_identity=not_ready,
        )


def test_gov_h_ready_primary_without_verified_evidence_still_rejects() -> None:
    ready_but_pending = replace(_real_actual(), model_release_evidence_reference="")
    assert ready_but_pending.primary_provider == "deepseek"
    assert ready_but_pending.primary_provider_ready is True
    with pytest.raises(RolloutActivationError, match="PENDING"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
            model_release_identity=ready_but_pending,
        )


def test_gov_h_deterministic_request_fallback_does_not_mutate_release_identity() -> None:
    class FailingDeepSeek:
        def ready(self):
            return True

        def analyze(self, text):
            raise ModelGatewayError("synthetic provider failure")

    identity = _real_actual()
    gateway = FallbackModelGateway(FailingDeepSeek(), DeterministicModelGateway())
    result = gateway.analyze("水管漏水需要报修")

    assert result.provider == "keyword"
    assert result.degraded is True
    assert identity.primary_provider == "deepseek"
    assert identity.primary_provider_ready is True


def test_gov_i_base_url_change_changes_fingerprint_and_fails_closed() -> None:
    certified = _provider_settings(base_url="https://api.deepseek.com")
    different = _provider_settings(base_url="https://different.example")
    assert provider_config_fingerprint(certified) != provider_config_fingerprint(different)
    # Minimal normalization: a trailing slash does not change the fingerprint.
    trailing = _provider_settings(base_url="https://api.deepseek.com/")
    assert provider_config_fingerprint(certified) == provider_config_fingerprint(trailing)
    fp_certified = provider_config_fingerprint(certified)
    fp_different = provider_config_fingerprint(different)
    actual = replace(_real_actual(), provider_config_fingerprint=fp_different)
    identity = replace(
        _identity(bps=500, release_sha=RELEASE_SHA),
        provider_config_fingerprint=fp_certified,
    )
    with pytest.raises(RolloutActivationError, match="provider_config_fingerprint"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=actual,
        )


def test_gov_j_timeout_change_changes_fingerprint_and_fails_closed() -> None:
    certified = _provider_settings(total_timeout=6.0)
    different = _provider_settings(total_timeout=10.0)
    assert provider_config_fingerprint(certified) != provider_config_fingerprint(different)
    fp_certified = provider_config_fingerprint(certified)
    fp_different = provider_config_fingerprint(different)
    actual = replace(_real_actual(), provider_config_fingerprint=fp_different)
    identity = replace(
        _identity(bps=500, release_sha=RELEASE_SHA),
        provider_config_fingerprint=fp_certified,
    )
    with pytest.raises(RolloutActivationError, match="provider_config_fingerprint"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=actual,
        )


@pytest.mark.parametrize(
    ("contract_name", "changed"),
    [
        ("DEEPSEEK_MAX_ATTEMPTS", 3),
        ("RETRY_POLICY_VERSION", "different-retry-policy-v2"),
        ("FALLBACK_ENABLED", False),
        ("FALLBACK_POLICY_VERSION", "different-fallback-policy-v2"),
        ("PROVIDER_RESPONSE_CONFIG_VERSION", "different-response-contract-v2"),
    ],
)
def test_gov_k_fingerprint_binds_fallback_retry_and_response_contract(
    monkeypatch, contract_name, changed
) -> None:
    import property_agent.agent.model_release_approval as approval_contract

    settings_obj = _provider_settings()
    effective = effective_provider_config(settings_obj)
    assert effective == {
        "primary_provider": PRIMARY_PROVIDER,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "connect_timeout_seconds": 3.0,
        "read_timeout_seconds": 12.0,
        "total_timeout_seconds": 6.0,
        "max_attempts": 2,
        "retry_policy_version": RETRY_POLICY_VERSION,
        "fallback_enabled": True,
        "fallback_policy_version": FALLBACK_POLICY_VERSION,
        "provider_response_config_version": PROVIDER_RESPONSE_CONFIG_VERSION,
    }

    certified = provider_config_fingerprint(settings_obj)
    monkeypatch.setattr(approval_contract, contract_name, changed)
    assert provider_config_fingerprint(settings_obj) != certified


def test_gov_l_secrets_excluded_from_identity_payload_and_audit() -> None:
    api_key = "sk-super-secret-value"
    settings_obj = _provider_settings(api_key=api_key)
    assert api_key not in provider_config_fingerprint(settings_obj)
    assert "api_key" not in json.dumps(effective_provider_config(settings_obj))
    assert "deepseek_api_key" not in ModelReleaseIdentity.__dataclass_fields__
    assert "secret_salt" not in ModelReleaseIdentity.__dataclass_fields__
    assert "secret_salt" not in RolloutReleaseIdentity.__dataclass_fields__
    actual = _real_actual()
    identity = _identity(bps=500, release_sha=RELEASE_SHA)
    assert api_key not in repr(actual)
    assert SALT.decode() not in repr(identity)
    events: list[RolloutAuditEvent] = []
    activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(identity),
        model_release_identity=actual,
        audit_sink=events.append,
    )
    assert api_key not in repr(events[0])
    assert SALT.decode() not in repr(events[0])
    first = _provider_settings()
    second = _provider_settings()
    first.agent_v2_rollout_salt = "first-secret-salt"
    second.agent_v2_rollout_salt = "second-secret-salt"
    assert provider_config_fingerprint(first) == provider_config_fingerprint(second)


# ═══════════════════════════════════════════════════════════════════════════
# Blocker 2 — activation audit must represent the real approved transition
# ═══════════════════════════════════════════════════════════════════════════


def test_blocker2_initial_zero_to_five_hundred_records_previous() -> None:
    identity = replace(
        _identity(bps=500, release_sha=RELEASE_SHA),
        previous_rollout_basis_points=0,
        previous_rollout_config_version=ROLLOUT_BASELINE_CONFIG_VERSION,
    )
    events: list[RolloutAuditEvent] = []
    activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(identity),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert events[0].old_basis_points == 0
    assert events[0].new_basis_points == 500
    assert events[0].old_config_version == ROLLOUT_BASELINE_CONFIG_VERSION
    assert events[0].new_config_version == "cfg-v1"


def test_blocker2_promotion_five_hundred_to_twenty_five_hundred() -> None:
    identity = replace(
        _identity(bps=2500, release_sha=RELEASE_SHA, config_version="r2-config"),
        previous_rollout_basis_points=500,
        previous_rollout_config_version="r1-config",
    )
    events: list[RolloutAuditEvent] = []
    activate_rollout_control(
        _config(2500, version="r2-config"),
        release_sha=RELEASE_SHA,
        manifest=_approved(identity),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert events[0].old_basis_points == 500
    assert events[0].new_basis_points == 2500
    assert events[0].old_config_version == "r1-config"
    assert events[0].new_config_version == "r2-config"


def test_blocker2_promotion_twenty_five_hundred_to_fifty_percent() -> None:
    identity = replace(
        _identity(bps=5000, release_sha=RELEASE_SHA, config_version="r3-config"),
        previous_rollout_basis_points=2500,
        previous_rollout_config_version="r2-config",
    )
    events: list[RolloutAuditEvent] = []
    activate_rollout_control(
        _config(5000, version="r3-config"),
        release_sha=RELEASE_SHA,
        manifest=_approved(identity),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert events[0].old_basis_points == 2500
    assert events[0].new_basis_points == 5000
    assert events[0].old_config_version == "r2-config"
    assert events[0].new_config_version == "r3-config"


def test_blocker2_twenty_five_percent_reports_real_previous_not_zero() -> None:
    # A 25% (2500 bps) promotion must report its real previous state (500), never a
    # fabricated old=0. The implementation uses the manifest's previous facts.
    identity = replace(
        _identity(bps=2500, release_sha=RELEASE_SHA, config_version="r2-config"),
        previous_rollout_basis_points=500,
        previous_rollout_config_version="r1-config",
    )
    events: list[RolloutAuditEvent] = []
    activate_rollout_control(
        _config(2500, version="r2-config"),
        release_sha=RELEASE_SHA,
        manifest=_approved(identity),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert events[0].old_basis_points == 500
    assert events[0].old_basis_points != 0


def test_blocker2_digest_changes_with_previous_state() -> None:
    base = _identity(bps=500, release_sha=RELEASE_SHA)
    initial = replace(
        base,
        previous_rollout_basis_points=0,
        previous_rollout_config_version=ROLLOUT_BASELINE_CONFIG_VERSION,
    )
    promoted = replace(
        base, previous_rollout_basis_points=500, previous_rollout_config_version="r1-config"
    )
    digest_initial = compute_manifest_sha256(
        RolloutActivationManifest(identity=initial, status=APPROVED)
    )
    digest_promoted = compute_manifest_sha256(
        RolloutActivationManifest(identity=promoted, status=APPROVED)
    )
    assert digest_initial != digest_promoted


# ═══════════════════════════════════════════════════════════════════════════
# Small hardening — approver placeholder + explicit manifest version
# ═══════════════════════════════════════════════════════════════════════════


def test_hardening_approver_placeholder_rejected() -> None:
    with pytest.raises(RolloutActivationError, match="approver_reference"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(
                replace(
                    _identity(bps=500, release_sha=RELEASE_SHA),
                    approver_reference="REPLACE_WITH_BOUNDED_APPROVER_REFERENCE",
                )
            ),
            model_release_identity=_real_actual(),
        )


def test_hardening_approver_unconfigured_rejected() -> None:
    with pytest.raises(RolloutActivationError, match="approver_reference"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(
                replace(
                    _identity(bps=500, release_sha=RELEASE_SHA),
                    approver_reference="unconfigured",
                )
            ),
            model_release_identity=_real_actual(),
        )


def test_hardening_explicit_manifest_version_required_for_approved() -> None:
    # An APPROVED manifest with no explicit activation_manifest_version must fail;
    # a missing version is not silently treated as the current supported version.
    identity = replace(_identity(bps=500, release_sha=RELEASE_SHA), activation_manifest_version="")
    with pytest.raises(RolloutActivationError, match="activation_manifest_version"):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=_approved(identity),
            model_release_identity=_real_actual(),
        )


def test_hardening_pending_manifest_without_version_stays_non_operational() -> None:
    # A PENDING example manifest may omit the version and remain non-operational.
    identity = replace(_identity(), activation_manifest_version="")
    manifest = RolloutActivationManifest(
        identity=identity, status=RolloutActivationManifestStatus.PENDING
    )
    with pytest.raises(RolloutActivationError):
        activate_rollout_control(
            _config(500),
            release_sha=RELEASE_SHA,
            manifest=manifest,
            model_release_identity=_real_actual(),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Gap 1 — positive acceptance: fresh approved identities (incl. promotion-only
# via the real deployment boundary, never via runtime apply)
# ═══════════════════════════════════════════════════════════════════════════


def test_activation_positive_five_percent() -> None:
    events: list[RolloutAuditEvent] = []
    control = activate_rollout_control(
        _config(500),
        release_sha=RELEASE_SHA,
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA, approver="ops:9")),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert control.config.basis_points == 500
    assert events[0].release_sha == RELEASE_SHA
    assert events[0].approver_reference == "ops:9"
    assert events[0].new_basis_points == 500


def test_activation_positive_ten_percent_via_fresh_boundary() -> None:
    # A brand-new approved identity at 1000 bps is accepted through the real
    # activation boundary (the ONLY promotion path). The runtime apply path
    # rejects the same increase.
    events: list[RolloutAuditEvent] = []
    control = activate_rollout_control(
        _config(1000),
        release_sha=RELEASE_SHA,
        manifest=_approved(_identity(bps=1000, release_sha=RELEASE_SHA, approver="ops:10")),
        model_release_identity=_real_actual(),
        audit_sink=events.append,
    )
    assert control.config.basis_points == 1000
    assert events[0].new_basis_points == 1000
    # The same increase cannot be reached through runtime apply.
    with pytest.raises(ValueError, match="new approved activation manifest"):
        control.apply(
            _config(2000, version="cfg-v2"),
            reason=RolloutChangeReason.APPROVED_PROMOTION,
            approver_reference="ops:11",
        )


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


def _settings(*, basis_points: int, release_sha: str = RELEASE_SHA) -> _FakeSettings:
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


def test_production_default_public_v2_rollout_remains_zero() -> None:
    field = Settings.model_fields["agent_v2_new_conversation_rollout_basis_points"]
    assert field.default == 0


def test_composition_nonzero_without_manifest_fails_closed() -> None:
    with pytest.raises(RolloutActivationError):
        build_rollout_control_from_settings(
            _settings(basis_points=500, release_sha=RELEASE_SHA),
            manifest=None,
            audit_sink=None,
        )


def test_composition_environment_restart_zero_to_five_percent_cannot_skip_audit() -> None:
    # A deployment that flips config 0 -> 500% without an approved manifest must
    # NOT silently start serving v2; the activation boundary is enforced.
    with pytest.raises(RolloutActivationError):
        build_rollout_control_from_settings(
            _settings(basis_points=500, release_sha=RELEASE_SHA),
            manifest=None,
            audit_sink=None,
        )


def test_composition_nonzero_with_approved_manifest_activates_and_audits() -> None:
    events: list[RolloutAuditEvent] = []
    control = build_rollout_control_from_settings(
        _settings(basis_points=500, release_sha=RELEASE_SHA),
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA, approver="ops:9")),
        audit_sink=events.append,
        model_release_identity=_real_actual(),
    )
    assert control.config.basis_points == 500
    assert len(events) == 1
    assert events[0].release_sha == RELEASE_SHA
    assert events[0].approver_reference == "ops:9"
    assert events[0].new_basis_points == 500


def test_composition_release_sha_mismatch_fails_closed() -> None:
    with pytest.raises(RolloutActivationError, match="does not match deployed"):
        build_rollout_control_from_settings(
            _settings(basis_points=500, release_sha=RELEASE_SHA),
            manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA_B)),
            audit_sink=None,
        )


def test_composition_audit_evidence_omits_secret_salt() -> None:
    events: list[RolloutAuditEvent] = []
    build_rollout_control_from_settings(
        _settings(basis_points=500, release_sha=RELEASE_SHA),
        manifest=_approved(_identity(bps=500, release_sha=RELEASE_SHA)),
        audit_sink=events.append,
        model_release_identity=_real_actual(),
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
