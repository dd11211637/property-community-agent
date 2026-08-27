"""Shared production-owned real-model approval + effective provider-config contract.

PR7-C model-release governance: ONE production contract answers "what counts as an
approved real-model release" and "what is the actual effective provider configuration".
Both PR7-B certification (``testing/pr7b/real_model_gate``) and PR7-C rollout activation
(``runtime_rollout_activation``) consume THIS contract. Production never imports
``testing/``; ``testing/`` imports production.

Trust chain (no self-referential operator strings):

    protected real-model approval artifact
        -> verify_approval_evidence()   (pure, fail-closed on anything short of APPROVED
                                         + verified artifact digest)
        -> VerifiedApprovalEvidence.evidence_reference  ("pr7b-real-model:<artifact_sha256>")
        -> ModelReleaseIdentity.model_release_evidence_reference
        -> RolloutActivationManifest must match it field-by-field

The effective provider configuration is captured in a canonical NON-SECRET form and
hashed into ``provider_config_fingerprint`` so a rollout cannot be certified against
one base_url/timeout set and run against another. API keys, authorization headers,
the rollout salt, JWT and other credentials are NEVER part of the fingerprint or any
identity field.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The one protected real-model baseline approval manifest version we accept.
BASELINE_APPROVAL_MANIFEST_VERSION = "pr7b-real-model-baseline-approval-v1"
# Canonical baseline approval file (committed; today approval_status=PENDING).
COMMITTED_BASELINE_APPROVAL_PATH = "config/pr7b_real_model_baseline_approval.json"
# DeepSeek gateway retries once -> two total attempts (deepseek-bounded-retry-v1).
DEEPSEEK_MAX_ATTEMPTS = 2

_SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")
_CONFIG_PREFIX = "config/"


@dataclass(frozen=True, slots=True)
class VerifiedApprovalEvidence:
    """A real-model approval whose manifest and artifact digest have BOTH been verified.

    ``evidence_reference`` is derived deterministically from the verified artifact
    digest (never operator-selected, never manifest-selected, never a bare source
    constant). It is the ONLY value that may appear as the actual
    ``model_release_evidence_reference`` for a non-zero rollout.
    """

    approval_manifest_version: str
    artifact_path: str
    artifact_sha256: str
    evidence_reference: str


def _repo_root() -> Path:
    """Repository root derived from this module's location (src/property_agent/agent/)."""
    return Path(__file__).resolve().parents[3]


def _within_config_boundary(artifact_path: Any) -> bool:
    """Bounded artifact path: relative, under config/, no parent traversal."""
    if not isinstance(artifact_path, str) or not artifact_path:
        return False
    normalized = artifact_path.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return False
    parts = normalized.split("/")
    if ".." in parts or "." in parts:
        return False
    return normalized.startswith(_CONFIG_PREFIX)


def verify_approval_evidence(
    approval: dict[str, Any],
    *,
    artifact_bytes: bytes | None,
) -> VerifiedApprovalEvidence | None:
    """Pure validator: APPROVED approval + verified artifact digest.

    Returns verified evidence ONLY when ALL of the following hold; otherwise returns
    ``None`` (non-approved) so callers fail closed:

    * ``approval_manifest_version`` == the one supported baseline approval version;
    * ``approval_status`` == ``APPROVED`` (PENDING / pending / UNAPPROVED / anything
      else is NOT approved evidence);
    * ``artifact_path`` is bounded within the repository ``config/`` boundary;
    * ``artifact_sha256`` is an exact 64-char lowercase SHA-256 hex;
    * the artifact bytes are present;
    * ``sha256(artifact_bytes)`` == ``artifact_sha256``.

    ``APPROVED`` alone is never sufficient evidence — the artifact digest MUST be
    verified. Never raises; a malformed/unverifiable approval is a non-approval.
    """
    if approval.get("approval_manifest_version") != BASELINE_APPROVAL_MANIFEST_VERSION:
        return None
    if approval.get("approval_status") != "APPROVED":
        return None
    artifact_path = approval.get("artifact_path")
    if not _within_config_boundary(artifact_path):
        return None
    expected = approval.get("artifact_sha256")
    if not isinstance(expected, str) or not _SHA256_HEX.fullmatch(expected):
        return None
    if artifact_bytes is None:
        return None
    actual = hashlib.sha256(artifact_bytes).hexdigest()
    if actual != expected:
        return None
    return VerifiedApprovalEvidence(
        approval_manifest_version=str(approval["approval_manifest_version"]),
        artifact_path=str(artifact_path),
        artifact_sha256=actual,
        evidence_reference=f"pr7b-real-model:{actual}",
    )


def verify_committed_baseline_approval(
    repo_root: Path | None = None,
) -> VerifiedApprovalEvidence | None:
    """Load + verify the committed baseline approval manifest against its artifact.

    The committed manifest is ``PENDING`` today, so this returns ``None`` (fail
    closed). Passing ``repo_root`` keeps the function pure for tests with tempfiles.
    Any I/O / parse failure is a non-approval, never a raise.
    """
    root = repo_root or _repo_root()
    approval_path = root / COMMITTED_BASELINE_APPROVAL_PATH
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    artifact_path = approval.get("artifact_path")
    if not _within_config_boundary(artifact_path):
        return None
    full = (root / str(artifact_path)).resolve()
    artifact_bytes = full.read_bytes() if full.is_file() else None
    return verify_approval_evidence(approval, artifact_bytes=artifact_bytes)


def actual_provider_class(settings: Any) -> str:
    """The ACTUAL running provider class for this deployment.

    ``build_model_gateway`` returns ``DeterministicModelGateway`` when no DeepSeek
    credential is configured, so a rollout must never bind to ``deepseek`` merely from
    a static source constant. Returns ``"deepseek"`` only when a credential exists,
    otherwise ``"deterministic"`` (the fallback that production actually runs).
    """
    if (getattr(settings, "deepseek_api_key", "") or "").strip():
        return "deepseek"
    return "deterministic"


def _normalized_base_url(settings: Any) -> str:
    """Canonical base URL: strip whitespace + trailing ``/`` (no aggressive URL
    normalization, so ``https://api.deepseek.com`` == ``https://api.deepseek.com/``)."""
    value = (getattr(settings, "deepseek_base_url", "") or "").strip()
    if value.endswith("/"):
        value = value.rstrip("/")
    return value


def effective_provider_config(settings: Any) -> dict[str, Any]:
    """Canonical NON-SECRET effective provider configuration (what actually runs).

    Only parameters that materially change certified provider behavior are included.
    API keys, authorization headers, the rollout salt, JWT and credentials are NEVER
    included.
    """
    return {
        "provider_class": actual_provider_class(settings),
        "base_url": _normalized_base_url(settings),
        "model": getattr(settings, "deepseek_model", "") or "",
        "connect_timeout_seconds": getattr(settings, "deepseek_connect_timeout_seconds", 0),
        "read_timeout_seconds": getattr(settings, "deepseek_read_timeout_seconds", 0),
        "total_timeout_seconds": getattr(settings, "deepseek_total_timeout_seconds", 0),
        "max_attempts": DEEPSEEK_MAX_ATTEMPTS,
    }


def provider_config_fingerprint(settings: Any) -> str:
    """SHA-256 of the canonical effective non-secret provider configuration."""
    payload = json.dumps(effective_provider_config(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
