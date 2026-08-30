"""Server-owned PR7-C rollout configuration, assignment, and audit contracts."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock
from uuid import UUID

logger = logging.getLogger(__name__)

_VERSION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}")
_OPERATOR_REFERENCE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}")
_MINIMUM_SALT_BYTES = 32

# Baseline config identity used when a fresh process activates a non-zero rollout
# from the implicit zero default. The secret salt is intentionally NEVER part of
# any activation identity or audit evidence.
ROLLOUT_BASELINE_CONFIG_VERSION = "pr7c-baseline-zero"

# The rollout activation boundary (manifest, release identity, SHA-256 integrity,
# and the only non-zero promotion path) lives in runtime_rollout_activation and
# is re-exported at the bottom of this module to preserve public import paths.


class EligibilityReason(StrEnum):
    """Bounded server-owned reasons for v2 eligibility."""

    ELIGIBLE = "eligible"
    EMERGENCY_STOP = "emergency_stop"
    API_SURFACE_UNSUPPORTED = "api_surface_unsupported"
    DEPLOYMENT_INCOMPATIBLE = "deployment_incompatible"
    V2_ENGINE_UNAVAILABLE = "v2_engine_unavailable"
    OFFICIAL_SAVER_UNAVAILABLE = "official_saver_unavailable"
    ACCEPTED_HEAD_UNAVAILABLE = "accepted_head_unavailable"
    MODEL_CONFIG_UNAPPROVED = "model_config_unapproved"
    MEMORY_EMBEDDING_UNAVAILABLE = "memory_embedding_unavailable"
    COMMUNITY_POLICY_EXCLUDED = "community_policy_excluded"


class BucketDecisionClass(StrEnum):
    """Bounded assignment outcome that is safe for metric labels."""

    ELIGIBILITY_FALLBACK = "eligibility_fallback"
    ROLLOUT_ZERO = "rollout_zero"
    BUCKET_V1 = "bucket_v1"
    BUCKET_V2 = "bucket_v2"
    LOCAL_V2_DEFAULT = "local_v2_default"


class RolloutChangeReason(StrEnum):
    """Audited operator reasons; free-form text is intentionally excluded."""

    APPROVED_PROMOTION = "approved_promotion"
    CONFIG_CORRECTION = "config_correction"
    INCIDENT_ROLLBACK = "incident_rollback"
    EMERGENCY_STOP = "emergency_stop"


@dataclass(frozen=True, slots=True)
class RuntimeEligibility:
    """Trusted structural facts used before a new conversation is pinned."""

    api_surface_supported: bool = True
    deployment_compatible: bool = True
    v2_engine_available: bool = False
    official_saver_available: bool = False
    accepted_head_available: bool = True
    model_config_approved: bool = False
    memory_embedding_available: bool = True
    community_policy_included: bool = True
    emergency_stop: bool = False

    def reason(self) -> EligibilityReason:
        checks = (
            (self.emergency_stop, EligibilityReason.EMERGENCY_STOP),
            (not self.api_surface_supported, EligibilityReason.API_SURFACE_UNSUPPORTED),
            (not self.deployment_compatible, EligibilityReason.DEPLOYMENT_INCOMPATIBLE),
            (not self.v2_engine_available, EligibilityReason.V2_ENGINE_UNAVAILABLE),
            (not self.official_saver_available, EligibilityReason.OFFICIAL_SAVER_UNAVAILABLE),
            (not self.accepted_head_available, EligibilityReason.ACCEPTED_HEAD_UNAVAILABLE),
            (not self.model_config_approved, EligibilityReason.MODEL_CONFIG_UNAPPROVED),
            (
                not self.memory_embedding_available,
                EligibilityReason.MEMORY_EMBEDDING_UNAVAILABLE,
            ),
            (not self.community_policy_included, EligibilityReason.COMMUNITY_POLICY_EXCLUDED),
        )
        for failed, reason in checks:
            if failed:
                return reason
        return EligibilityReason.ELIGIBLE


@dataclass(frozen=True, slots=True)
class RolloutConfig:
    """Validated rollout config. The secret salt is excluded from repr and evidence."""

    basis_points: int = 0
    secret_salt: bytes = field(default=b"", repr=False)
    salt_version: str = "unconfigured"
    config_version: str = "pr7c-default-v1"
    eligibility_policy_version: str = "pr7c-eligibility-v1"
    fallback_runtime: str = "v1"
    model_approval_id: str = "unconfigured"
    prompt_contract_version: str = "unconfigured"

    def __post_init__(self) -> None:
        if not 0 <= self.basis_points <= 10_000:
            raise ValueError("rollout basis points must be between 0 and 10000")
        for name, value in (
            ("salt version", self.salt_version),
            ("config version", self.config_version),
            ("eligibility policy version", self.eligibility_policy_version),
        ):
            if not _VERSION.fullmatch(value):
                raise ValueError(f"invalid {name}")
        # Model/prompt approval identities are bounded opaque identifiers (a real
        # id may carry a provider qualifier such as "model:deepseek-v4"). They are
        # validated with the same bounded-opaque rule as operator references, not
        # the strict version rule. An EMPTY model_approval_id is the legitimate
        # pre-approval state (the actual evidence reference is empty while the
        # protected real-model baseline approval is PENDING); non-zero activation
        # still requires _is_real_approval_id at the activation boundary.
        for name, value in (
            ("model approval id", self.model_approval_id),
            ("prompt contract version", self.prompt_contract_version),
        ):
            if value and not _OPERATOR_REFERENCE.fullmatch(value):
                raise ValueError(f"invalid {name}")
        if self.fallback_runtime != "v1":
            raise ValueError("PR7-C safe new-conversation fallback must remain v1")
        if self.basis_points > 0 and len(self.secret_salt) < _MINIMUM_SALT_BYTES:
            raise ValueError("non-zero rollout requires a secret salt of at least 32 bytes")

    def bucket(self, *, community_id: UUID, actor_id: UUID, conversation_id: str) -> int:
        if not self.secret_salt:
            raise ValueError("rollout salt is unavailable")
        identity = f"{community_id}:{actor_id}:{conversation_id}".encode()
        digest = hmac.new(self.secret_salt, identity, hashlib.sha256).digest()
        return int.from_bytes(digest, "big") % 10_000


@dataclass(frozen=True, slots=True)
class RuntimeAssignment:
    """Bounded observable decision; it never contains identity or the rollout salt."""

    runtime_version: str
    eligible: bool
    eligibility_reason: EligibilityReason
    decision_class: BucketDecisionClass
    bucket: int | None
    rollout_basis_points: int
    config_version: str
    salt_version: str
    eligibility_policy_version: str


@dataclass(frozen=True, slots=True)
class RolloutAuditEvent:
    """Explicit config transition evidence suitable for an audit sink.

    ``release_sha`` binds the transition to an exact deployed release. The secret
    rollout salt is never placed in this structure. ``approver_reference`` and
    ``change_reference`` are bounded opaque identifiers (no PII) so activation
    and rollback are both attributable without leaking operator identity text.
    """

    old_basis_points: int
    new_basis_points: int
    old_config_version: str
    new_config_version: str
    reason: RolloutChangeReason
    approver_reference: str
    change_reference: str
    changed_at: str
    release_sha: str = ""


AuditSink = Callable[[RolloutAuditEvent], None]


class RolloutControl:
    """Thread-safe explicit config control with no timer or auto-promotion path."""

    def __init__(self, config: RolloutConfig, *, audit_sink: AuditSink | None = None) -> None:
        self._config = config
        self._audit_sink = audit_sink or _log_audit_event
        self._lock = RLock()
        # Release SHA established by the approved activation; carried into every
        # subsequent audit event (apply / rollback) for that control instance.
        self._release_sha = ""

    @property
    def config(self) -> RolloutConfig:
        with self._lock:
            return self._config

    def apply(
        self,
        config: RolloutConfig,
        *,
        reason: RolloutChangeReason,
        approver_reference: str,
        change_reference: str = "",
    ) -> RolloutAuditEvent:
        if not _OPERATOR_REFERENCE.fullmatch(approver_reference):
            raise ValueError("approver reference must be a bounded opaque identifier")
        # change_reference is optional; when present it must still be bounded opaque.
        if change_reference and not _OPERATOR_REFERENCE.fullmatch(change_reference):
            raise ValueError("change reference must be a bounded opaque identifier")
        with self._lock:
            previous = self._config
            # Promotion is NEVER an in-process authority. A runtime increase can
            # only occur through a brand-new approved activation manifest crossing
            # the real deployment boundary (activate_rollout_control), not here.
            if config.basis_points > previous.basis_points:
                raise ValueError("rollout increase requires a new approved activation manifest")
            event = _audit_event(
                previous,
                config,
                reason,
                approver_reference,
                change_reference=change_reference,
                release_sha=self._release_sha,
            )
            self._audit_sink(event)
            self._config = config
            return event

    def rollback_to_zero(
        self,
        *,
        config_version: str,
        reason: RolloutChangeReason,
        approver_reference: str,
        change_reference: str = "",
    ) -> RolloutAuditEvent:
        target = replace(self.config, basis_points=0, config_version=config_version)
        return self.apply(
            target,
            reason=reason,
            approver_reference=approver_reference,
            change_reference=change_reference,
        )

    def record_activation(self, identity: RolloutReleaseIdentity) -> RolloutAuditEvent:
        """Emit the audit transition for an approved non-zero activation.

        The transition is recorded from the approved transition's ``previous_*`` facts
        to its target (``rollout_basis_points`` / ``rollout_config_version``), never
        synthesized as zero. The previous/target facts live inside the canonical
        manifest SHA-256 payload, so the digest binds them. A first-canary transition
        is represented explicitly by a manifest whose ``previous_rollout_basis_points``
        is ``0`` and ``previous_rollout_config_version`` is ``pr7c-baseline-zero``.
        """
        event = RolloutAuditEvent(
            old_basis_points=identity.previous_rollout_basis_points,
            new_basis_points=identity.rollout_basis_points,
            old_config_version=identity.previous_rollout_config_version,
            new_config_version=identity.rollout_config_version,
            reason=RolloutChangeReason.APPROVED_PROMOTION,
            approver_reference=identity.approver_reference,
            change_reference="",
            changed_at=datetime.now(timezone.utc).isoformat(),
            release_sha=identity.release_sha,
        )
        self._release_sha = identity.release_sha
        self._audit_sink(event)
        return event


def decide_assignment(
    config: RolloutConfig,
    eligibility: RuntimeEligibility,
    *,
    community_id: UUID,
    actor_id: UUID,
    conversation_id: str,
) -> RuntimeAssignment:
    """Select a runtime for one not-yet-persisted trusted conversation identity."""
    reason = eligibility.reason()
    if reason is not EligibilityReason.ELIGIBLE:
        return _assignment(config, config.fallback_runtime, reason, None)
    if config.basis_points == 0:
        return _assignment(config, "v1", reason, None)
    bucket = config.bucket(
        community_id=community_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
    )
    return _assignment(config, "v2" if bucket < config.basis_points else "v1", reason, bucket)


def _assignment(
    config: RolloutConfig,
    runtime_version: str,
    reason: EligibilityReason,
    bucket: int | None,
) -> RuntimeAssignment:
    if reason is not EligibilityReason.ELIGIBLE:
        decision_class = BucketDecisionClass.ELIGIBILITY_FALLBACK
    elif bucket is None:
        decision_class = BucketDecisionClass.ROLLOUT_ZERO
    elif runtime_version == "v2":
        decision_class = BucketDecisionClass.BUCKET_V2
    else:
        decision_class = BucketDecisionClass.BUCKET_V1
    return RuntimeAssignment(
        runtime_version=runtime_version,
        eligible=reason is EligibilityReason.ELIGIBLE,
        eligibility_reason=reason,
        decision_class=decision_class,
        bucket=bucket,
        rollout_basis_points=config.basis_points,
        config_version=config.config_version,
        salt_version=config.salt_version,
        eligibility_policy_version=config.eligibility_policy_version,
    )


def _audit_event(
    previous: RolloutConfig,
    current: RolloutConfig,
    reason: RolloutChangeReason,
    approver_reference: str,
    *,
    change_reference: str = "",
    release_sha: str = "",
) -> RolloutAuditEvent:
    return RolloutAuditEvent(
        old_basis_points=previous.basis_points,
        new_basis_points=current.basis_points,
        old_config_version=previous.config_version,
        new_config_version=current.config_version,
        reason=reason,
        approver_reference=approver_reference,
        change_reference=change_reference,
        changed_at=datetime.now(timezone.utc).isoformat(),
        release_sha=release_sha,
    )


def _log_audit_event(event: RolloutAuditEvent) -> None:
    logger.info(
        "agent rollout config changed old_bps=%s new_bps=%s old_version=%s "
        "new_version=%s reason=%s approver_ref=%s change_ref=%s changed_at=%s release_sha=%s",
        event.old_basis_points,
        event.new_basis_points,
        event.old_config_version,
        event.new_config_version,
        event.reason.value,
        event.approver_reference,
        event.change_reference,
        event.changed_at,
        event.release_sha,
    )


# Compatibility facade: the activation boundary was extracted into
# runtime_rollout_activation to keep this module within repository structure
# limits. Re-export its public symbols so existing import paths are preserved.
from property_agent.agent.runtime_rollout_activation import (  # noqa: E402
    RolloutActivationError,
    RolloutActivationManifest,
    RolloutActivationManifestStatus,
    RolloutReleaseIdentity,
    activate_rollout_control,
    approval_signature_payload,
    compute_manifest_sha256,
    load_rollout_activation_manifest,
    parse_rollout_activation_manifest,
    verify_manifest_approval_authority,
    verify_manifest_integrity,
)

__all__ = [
    "BucketDecisionClass",
    "EligibilityReason",
    "RolloutActivationError",
    "RolloutActivationManifest",
    "RolloutActivationManifestStatus",
    "RolloutAuditEvent",
    "RolloutChangeReason",
    "RolloutConfig",
    "RolloutControl",
    "RolloutReleaseIdentity",
    "RuntimeAssignment",
    "RuntimeEligibility",
    "activate_rollout_control",
    "approval_signature_payload",
    "compute_manifest_sha256",
    "decide_assignment",
    "load_rollout_activation_manifest",
    "parse_rollout_activation_manifest",
    "verify_manifest_integrity",
    "verify_manifest_approval_authority",
]
