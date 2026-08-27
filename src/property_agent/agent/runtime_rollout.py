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
    COMMUNITY_POLICY_EXCLUDED = "community_policy_excluded"


class BucketDecisionClass(StrEnum):
    """Bounded assignment outcome that is safe for metric labels."""

    ELIGIBILITY_FALLBACK = "eligibility_fallback"
    ROLLOUT_ZERO = "rollout_zero"
    BUCKET_V1 = "bucket_v1"
    BUCKET_V2 = "bucket_v2"


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
    """Explicit config transition evidence suitable for an audit sink."""

    old_basis_points: int
    new_basis_points: int
    old_config_version: str
    new_config_version: str
    reason: RolloutChangeReason
    operator_reference: str
    changed_at: str


AuditSink = Callable[[RolloutAuditEvent], None]


class RolloutControl:
    """Thread-safe explicit config control with no timer or auto-promotion path."""

    def __init__(self, config: RolloutConfig, *, audit_sink: AuditSink | None = None) -> None:
        self._config = config
        self._audit_sink = audit_sink or _log_audit_event
        self._lock = RLock()

    @property
    def config(self) -> RolloutConfig:
        with self._lock:
            return self._config

    def apply(
        self,
        config: RolloutConfig,
        *,
        reason: RolloutChangeReason,
        operator_reference: str,
        promotion_approved: bool = False,
    ) -> RolloutAuditEvent:
        if not _OPERATOR_REFERENCE.fullmatch(operator_reference):
            raise ValueError("operator reference must be a bounded opaque identifier")
        with self._lock:
            previous = self._config
            if config.basis_points > previous.basis_points and not promotion_approved:
                raise ValueError("rollout increase requires explicit approval")
            event = _audit_event(previous, config, reason, operator_reference)
            self._audit_sink(event)
            self._config = config
            return event

    def rollback_to_zero(
        self,
        *,
        config_version: str,
        reason: RolloutChangeReason,
        operator_reference: str,
    ) -> RolloutAuditEvent:
        target = replace(self.config, basis_points=0, config_version=config_version)
        return self.apply(
            target,
            reason=reason,
            operator_reference=operator_reference,
        )


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
    operator_reference: str,
) -> RolloutAuditEvent:
    return RolloutAuditEvent(
        old_basis_points=previous.basis_points,
        new_basis_points=current.basis_points,
        old_config_version=previous.config_version,
        new_config_version=current.config_version,
        reason=reason,
        operator_reference=operator_reference,
        changed_at=datetime.now(timezone.utc).isoformat(),
    )


def _log_audit_event(event: RolloutAuditEvent) -> None:
    logger.info(
        "agent rollout config changed old_bps=%s new_bps=%s old_version=%s "
        "new_version=%s reason=%s operator_ref=%s changed_at=%s",
        event.old_basis_points,
        event.new_basis_points,
        event.old_config_version,
        event.new_config_version,
        event.reason.value,
        event.operator_reference,
        event.changed_at,
    )


__all__ = [
    "BucketDecisionClass",
    "EligibilityReason",
    "RolloutAuditEvent",
    "RolloutChangeReason",
    "RolloutConfig",
    "RolloutControl",
    "RuntimeAssignment",
    "RuntimeEligibility",
    "decide_assignment",
]
