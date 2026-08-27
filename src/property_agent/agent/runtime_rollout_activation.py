"""PR7-C rollout activation boundary: manifest, release identity, SHA-256 integrity.

This module is the ONLY production path through which a non-zero rollout may
become active. It validates the deployment-provided ``RolloutActivationManifest``
against the running release and the active server-owned configuration, failing
closed on any mismatch (Blockers 1, 2, 3).

The runtime assignment/control logic (``RolloutControl``, ``RolloutConfig``,
``decide_assignment``) lives in ``runtime_rollout`` and imports this boundary
through a facade, so public import paths are preserved.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from property_agent.agent.model_release import (
    ModelReleaseIdentity as _ActualModelReleaseIdentity,
)
from property_agent.agent.model_release import actual_model_release_identity
from property_agent.agent.runtime_rollout import (
    _OPERATOR_REFERENCE,
    ROLLOUT_BASELINE_CONFIG_VERSION,
    AuditSink,
    RolloutConfig,
    RolloutControl,
)

logger = logging.getLogger(__name__)

# Exact Git commit identity is required for BOTH the deployed release and the
# approved activation manifest: no abbreviations, no operator placeholders.
_RELEASE_SHA = re.compile(r"^[a-f0-9]{40}$")
_SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")
# Model/prompt approval identities must be real bounded ids, never the literal
# operator placeholders shipped inside the example manifest, and never an
# un-verified approval sentinel (PENDING / UNAPPROVED / unconfigured / empty).
_PLACEHOLDER_PREFIX = "REPLACE_WITH"
_UNCONFIGURED_APPROVAL = "unconfigured"
# Values that describe a NOT-approved approval must fail closed even though they are
# bounded strings: equality of such strings can never constitute approval evidence.
_NON_APPROVAL_SENTINELS = frozenset({"pending", "unapproved", "unconfigured", "none", "null", ""})

ROLLOUT_ACTIVATION_MANIFEST_VERSION = "pr7c-activation-v2"
SUPPORTED_ACTIVATION_MANIFEST_VERSIONS = frozenset({ROLLOUT_ACTIVATION_MANIFEST_VERSION})


class RolloutActivationError(RuntimeError):
    """Raised when a non-zero rollout cannot be authorized by an approved identity."""


class RolloutActivationManifestStatus(StrEnum):
    """Lifecycle of a deployment-provided rollout activation manifest."""

    APPROVED = "approved"
    PENDING = "pending"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class RolloutReleaseIdentity:
    """Canonical, auditable rollout release/config identity.

    Every field is server-owned and bounded; the secret rollout salt is NEVER here.
    ``primary_provider``/``model``/``provider_config_version``/``provider_config_fingerprint``/
    ``fallback_policy_version``/``prompt_contract_version`` are bound against the shared
    ``ModelReleaseIdentity`` (the certified contract). ``model_release_evidence_reference`` and
    ``model_approval_id`` must both equal the SAME verified evidence reference (single approval
    authority). The ``previous_*``/target transition facts live inside the canonical SHA-256
    payload so the digest binds them; ``record_activation`` emits exactly these.
    """

    release_sha: str
    rollout_config_version: str
    rollout_basis_points: int
    salt_version: str
    eligibility_policy_version: str
    approved_fallback_runtime: str
    model_approval_id: str
    prompt_contract_version: str
    approver_reference: str
    approved_at: str
    activation_manifest_version: str = ROLLOUT_ACTIVATION_MANIFEST_VERSION
    # Certified provider contract. Readiness is an independent activation gate,
    # intentionally excluded from this signed manifest.
    primary_provider: str = "deepseek"
    model: str = ""
    provider_config_version: str = "deepseek-bounded-retry-v1"
    provider_config_fingerprint: str = ""
    fallback_policy_version: str = ""
    # Real verified model/release evidence reference (must equal the actual identity).
    model_release_evidence_reference: str = ""
    # Explicit approved transition identity (previous -> target rollout).
    previous_rollout_basis_points: int = 0
    previous_rollout_config_version: str = ROLLOUT_BASELINE_CONFIG_VERSION


@dataclass(frozen=True, slots=True)
class RolloutActivationManifest:
    """Deployment-time versioned activation manifest verified at startup."""

    identity: RolloutReleaseIdentity
    status: RolloutActivationManifestStatus
    manifest_sha256: str = ""


def parse_rollout_activation_manifest(data: dict) -> RolloutActivationManifest:
    """Parse a manifest dict; raises ValueError on malformed structured fields."""
    raw = data.get("identity", {})
    if not isinstance(raw, dict):
        raise ValueError("rollout activation manifest 'identity' must be an object")
    identity = RolloutReleaseIdentity(
        release_sha=str(raw.get("release_sha", "")),
        rollout_config_version=str(raw.get("rollout_config_version", "")),
        rollout_basis_points=int(raw.get("rollout_basis_points", 0)),
        salt_version=str(raw.get("salt_version", "")),
        eligibility_policy_version=str(raw.get("eligibility_policy_version", "")),
        approved_fallback_runtime=str(raw.get("approved_fallback_runtime", "v1")),
        model_approval_id=str(raw.get("model_approval_id", "")),
        prompt_contract_version=str(raw.get("prompt_contract_version", "")),
        approver_reference=str(raw.get("approver_reference", "")),
        approved_at=str(raw.get("approved_at", "")),
        # Hardening B: an APPROVED manifest must supply an explicit version. Do NOT
        # silently treat a missing version as the current supported version.
        activation_manifest_version=str(raw.get("activation_manifest_version", "")),
        primary_provider=str(raw.get("primary_provider", "")),
        model=str(raw.get("model", "")),
        provider_config_version=str(raw.get("provider_config_version", "")),
        provider_config_fingerprint=str(raw.get("provider_config_fingerprint", "")),
        fallback_policy_version=str(raw.get("fallback_policy_version", "")),
        model_release_evidence_reference=str(raw.get("model_release_evidence_reference", "")),
        previous_rollout_basis_points=int(raw.get("previous_rollout_basis_points", 0)),
        previous_rollout_config_version=str(
            raw.get("previous_rollout_config_version", ROLLOUT_BASELINE_CONFIG_VERSION)
        ),
    )
    status = RolloutActivationManifestStatus(str(data.get("status", "pending")))
    return RolloutActivationManifest(
        identity=identity,
        status=status,
        manifest_sha256=str(data.get("manifest_sha256", "")),
    )


def load_rollout_activation_manifest(path: str) -> RolloutActivationManifest | None:
    """Load an activation manifest from disk.

    Returns ``None`` when the file is missing or structurally invalid so that the
    caller can fail closed on a non-zero rollout. Never raises for I/O or parse
    errors — a broken manifest is treated as "no approved identity".
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return parse_rollout_activation_manifest(json.load(handle))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("invalid rollout activation manifest at %s: %s", path, exc)
        return None


def _is_real_approval_id(value: str) -> bool:
    """A model/prompt approval id is real only when bounded and not a placeholder
    and not an un-verified approval sentinel (PENDING / UNAPPROVED / unconfigured /
    empty). String equality between such values can never constitute approval."""
    if not value or not _OPERATOR_REFERENCE.fullmatch(value):
        return False
    if value.lower() in _NON_APPROVAL_SENTINELS:
        return False
    if value.upper().startswith(_PLACEHOLDER_PREFIX):
        return False
    return True


def _valid_utc(value: str) -> bool:
    """True when ``value`` is a timezone-aware, UTC ISO-8601 timestamp."""
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.utcoffset() == timedelta(0)


def _canonical_approval_payload(manifest: RolloutActivationManifest) -> str:
    """Deterministic JSON of the immutable approval payload (excludes the digest)."""
    identity = manifest.identity
    payload = {
        "identity": {
            "release_sha": identity.release_sha,
            "rollout_config_version": identity.rollout_config_version,
            "rollout_basis_points": identity.rollout_basis_points,
            "salt_version": identity.salt_version,
            "eligibility_policy_version": identity.eligibility_policy_version,
            "approved_fallback_runtime": identity.approved_fallback_runtime,
            "model_approval_id": identity.model_approval_id,
            "prompt_contract_version": identity.prompt_contract_version,
            "approver_reference": identity.approver_reference,
            "approved_at": identity.approved_at,
            "activation_manifest_version": identity.activation_manifest_version,
            "primary_provider": identity.primary_provider,
            "model": identity.model,
            "provider_config_version": identity.provider_config_version,
            "provider_config_fingerprint": identity.provider_config_fingerprint,
            "fallback_policy_version": identity.fallback_policy_version,
            "model_release_evidence_reference": identity.model_release_evidence_reference,
            "previous_rollout_basis_points": identity.previous_rollout_basis_points,
            "previous_rollout_config_version": identity.previous_rollout_config_version,
        },
        "status": manifest.status.value,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_manifest_sha256(manifest: RolloutActivationManifest) -> str:
    """SHA-256 of the canonical approval payload (deployment-side digest tool)."""
    return hashlib.sha256(_canonical_approval_payload(manifest).encode("utf-8")).hexdigest()


def verify_manifest_integrity(manifest: RolloutActivationManifest) -> None:
    """Fail closed unless ``manifest_sha256`` is the SHA-256 of the payload."""
    digest = manifest.manifest_sha256
    if not _SHA256_HEX.fullmatch(digest):
        raise RolloutActivationError(
            "rollout activation manifest_sha256 must be a 64-char lowercase hex digest"
        )
    expected = compute_manifest_sha256(manifest)
    if not hmac.compare_digest(expected, digest):
        raise RolloutActivationError(
            "rollout activation manifest_sha256 does not match the canonical approval payload"
        )


def _require(condition: bool, message: str) -> None:
    """Raise ``RolloutActivationError`` unless ``condition`` holds (fail closed)."""
    if not condition:
        raise RolloutActivationError(message)


def _validate_release_identity(
    identity: RolloutReleaseIdentity, manifest: RolloutActivationManifest, *, running_sha: str
) -> None:
    """Fail closed on Blocker 1 (exact 40-hex SHAs) and Blocker 3 (SHA-256)."""
    # Blocker 1 — exact 40-hex Git SHA on BOTH sides, matched exactly.
    _require(
        _RELEASE_SHA.fullmatch(running_sha),
        "deployed release_sha must be a 40-char lowercase git commit SHA",
    )
    _require(
        _RELEASE_SHA.fullmatch(identity.release_sha),
        "approved manifest release_sha must be a 40-char lowercase git commit SHA",
    )
    _require(
        identity.release_sha == running_sha,
        f"activation manifest release_sha {identity.release_sha!r} "
        f"does not match deployed release {running_sha!r}",
    )
    # Blocker 3 — SHA-256 integrity of the canonical approval payload.
    verify_manifest_integrity(manifest)


def _validate_activation_config_match(
    config: RolloutConfig, identity: RolloutReleaseIdentity
) -> None:
    """Fail closed on full identity match vs the active server-owned configuration.

    Covers config_version, basis_points, salt_version, eligibility_policy_version,
    approved_fallback_runtime, supported activation_manifest_version, bounded
    approver_reference (Hardening A: no placeholder/unconfigured), valid UTC
    approved_at, and a real model/prompt id on the active config.
    """
    _require(
        identity.rollout_config_version == config.config_version,
        f"activation manifest config_version {identity.rollout_config_version!r} "
        f"does not match active {config.config_version!r}",
    )
    _require(
        identity.rollout_basis_points == config.basis_points,
        f"activation manifest basis_points {identity.rollout_basis_points} "
        f"does not match active {config.basis_points}",
    )
    _require(
        identity.salt_version == config.salt_version,
        f"activation manifest salt_version {identity.salt_version!r} "
        f"does not match active {config.salt_version!r}",
    )
    _require(
        identity.eligibility_policy_version == config.eligibility_policy_version,
        f"activation manifest eligibility_policy_version {identity.eligibility_policy_version!r} "
        f"does not match active {config.eligibility_policy_version!r}",
    )
    _require(
        identity.approved_fallback_runtime == config.fallback_runtime,
        f"activation manifest approved_fallback_runtime {identity.approved_fallback_runtime!r} "
        f"does not match active {config.fallback_runtime!r}",
    )
    _require(
        identity.activation_manifest_version in SUPPORTED_ACTIVATION_MANIFEST_VERSIONS,
        f"unsupported activation_manifest_version {identity.activation_manifest_version!r}",
    )
    _require(
        _OPERATOR_REFERENCE.fullmatch(identity.approver_reference),
        "manifest approver_reference must be a bounded opaque identifier",
    )
    # Hardening A — reject committed placeholder / unconfigured approver values even
    # though they satisfy the bounded-opaque regex.
    _require(
        not identity.approver_reference.upper().startswith(_PLACEHOLDER_PREFIX),
        "manifest approver_reference must not be a placeholder value",
    )
    _require(
        identity.approver_reference != _UNCONFIGURED_APPROVAL,
        "manifest approver_reference must not be unconfigured",
    )
    _require(
        _valid_utc(identity.approved_at),
        "manifest approved_at must be a valid UTC ISO-8601 timestamp",
    )
    # Defense in depth: the active config itself must carry a real model/prompt id.
    _require(
        _is_real_approval_id(config.model_approval_id),
        "active config model_approval_id must be a real server-owned approval id",
    )
    _require(
        _is_real_approval_id(config.prompt_contract_version),
        "active config prompt_contract_version must be a real server-owned approval id",
    )


def _validate_activation_model_release(
    identity: RolloutReleaseIdentity, *, model_release_identity: _ActualModelReleaseIdentity
) -> None:
    """Fail closed: bind to the certified model execution contract.

    Provider, model, config, prompt, and verified approval facts must all match the
    server-owned ``ModelReleaseIdentity``. Readiness is checked independently.
    """
    actual = model_release_identity
    _require(
        identity.primary_provider == actual.primary_provider,
        f"activation manifest primary_provider {identity.primary_provider!r} "
        f"does not match actual {actual.primary_provider!r}",
    )
    # The certified DeepSeek primary must be constructible. Identity stays fixed;
    # unavailable credentials make the release ineligible rather than deterministic.
    _require(
        actual.primary_provider_ready,
        "certified primary provider is not ready "
        "(DeepSeek credential unavailable); non-zero rollout fails closed",
    )
    _require(
        identity.fallback_policy_version == actual.fallback_policy_version,
        f"activation manifest fallback_policy_version {identity.fallback_policy_version!r} "
        f"does not match actual {actual.fallback_policy_version!r}",
    )
    _require(
        identity.model == actual.model,
        f"activation manifest model {identity.model!r} "
        f"does not match actual configured model {actual.model!r}",
    )
    _require(
        identity.provider_config_version == actual.provider_config_version,
        f"activation manifest provider_config_version {identity.provider_config_version!r} "
        f"does not match actual {actual.provider_config_version!r}",
    )
    _require(
        _SHA256_HEX.fullmatch(identity.provider_config_fingerprint),
        "manifest provider_config_fingerprint must be a 64-char lowercase SHA-256 hex digest",
    )
    _require(
        identity.provider_config_fingerprint == actual.provider_config_fingerprint,
        f"activation manifest provider_config_fingerprint {identity.provider_config_fingerprint!r} "
        f"does not match actual {actual.provider_config_fingerprint!r}",
    )
    _require(
        identity.prompt_contract_version == actual.prompt_contract_version,
        f"activation manifest prompt_contract_version {identity.prompt_contract_version!r} "
        f"does not match actual {actual.prompt_contract_version!r}",
    )
    _require(
        _is_real_approval_id(actual.model_release_evidence_reference),
        "actual model_release_evidence_reference is not a verified approval reference "
        "(REAL_MODEL_BASELINE_APPROVAL=PENDING)",
    )
    _require(
        _is_real_approval_id(identity.model_release_evidence_reference),
        "manifest model_release_evidence_reference must be a real bounded approval identifier",
    )
    _require(
        identity.model_release_evidence_reference == actual.model_release_evidence_reference,
        f"activation manifest model_release_evidence_reference "
        f"{identity.model_release_evidence_reference!r} does not match actual verified "
        f"{actual.model_release_evidence_reference!r}",
    )
    # model_approval_id binds to the SAME verified evidence authority (no second
    # approval authority).
    _require(
        _is_real_approval_id(identity.model_approval_id),
        "manifest model_approval_id must be a real bounded approval identifier",
    )
    _require(
        identity.model_approval_id == actual.model_release_evidence_reference,
        f"activation manifest model_approval_id {identity.model_approval_id!r} "
        f"does not match the actual verified model/release evidence reference "
        f"{actual.model_release_evidence_reference!r}",
    )


def _validate_activation_identity(
    config: RolloutConfig,
    identity: RolloutReleaseIdentity,
    *,
    model_release_identity: _ActualModelReleaseIdentity,
) -> None:
    """Fail closed on Blocker 2 (config match) and Blocker 1 (actual model binding)."""
    _validate_activation_config_match(config, identity)
    _validate_activation_model_release(identity, model_release_identity=model_release_identity)


def activate_rollout_control(
    config: RolloutConfig,
    *,
    release_sha: str | None,
    manifest: RolloutActivationManifest | None,
    audit_sink: AuditSink | None = None,
    model_release_identity: _ActualModelReleaseIdentity | None = None,
) -> RolloutControl:
    """Production rollout activation boundary.

    The ONLY path through which a non-zero rollout may become active. A rollout of
    zero basis points needs no activation identity and is returned as-is. Any
    non-zero rollout MUST be backed by an ``APPROVED`` manifest whose:

    * deployed ``release_sha`` AND manifest ``identity.release_sha`` are both
      exact 40-hex Git commit identities and match exactly (Blocker 1);
    * ``manifest_sha256`` is the SHA-256 of the canonical approval payload
      (Blocker 3);
    * complete ``RolloutReleaseIdentity`` matches the active configuration
      field-by-field, with a bounded ``approver_reference``, a valid UTC
      ``approved_at``, and real model/prompt approval identities (Blocker 2);
    * provider/model/provider-config/prompt-contract facts, the effective
      provider-config fingerprint, and the real verified model/release evidence
      reference match the ACTUAL running ``ModelReleaseIdentity`` (Blocker 1 — no
      self-referential operator string, no un-verified PENDING equality, no forged
      evidence reference).

    ``model_release_identity`` is the actual running release identity; when omitted it
    defaults to ``actual_model_release_identity()``. The evidence reference is derived
    from the protected real-model baseline approval artifact; while that baseline is
    PENDING the reference is empty, so any non-zero rollout remains fail-closed until
    the protected real-model baseline approval records verified evidence.

    Otherwise this fails closed by raising ``RolloutActivationError``. A fresh
    process starting directly at a non-zero rollout therefore cannot silently
    bypass the audit/release identity.
    """
    control = RolloutControl(config, audit_sink=audit_sink)
    if config.basis_points == 0:
        return control
    if manifest is None or manifest.status != RolloutActivationManifestStatus.APPROVED:
        raise RolloutActivationError(
            "non-zero rollout_basis_points requires an APPROVED RolloutActivationManifest"
        )
    running_sha = release_sha or ""
    _validate_release_identity(manifest.identity, manifest, running_sha=running_sha)
    identity = model_release_identity or actual_model_release_identity()
    _validate_activation_identity(config, manifest.identity, model_release_identity=identity)
    control.record_activation(manifest.identity)
    return control


__all__ = [
    "RolloutActivationError",
    "RolloutActivationManifest",
    "RolloutActivationManifestStatus",
    "RolloutReleaseIdentity",
    "activate_rollout_control",
    "compute_manifest_sha256",
    "load_rollout_activation_manifest",
    "parse_rollout_activation_manifest",
    "verify_manifest_integrity",
]
