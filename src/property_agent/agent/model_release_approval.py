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
# The CERTIFIED primary model execution contract. This is the release a rollout is
# certified against; it is NOT dynamically swapped to whichever provider happened to
# answer an individual request. DeepSeek is the certified primary; the deterministic
# gateway is a fallback, whose policy is itself part of the provider config contract.
PRIMARY_PROVIDER = "deepseek"
# Bounded retry contract: the DeepSeek gateway retries once (two total attempts).
DEEPSEEK_MAX_ATTEMPTS = 2
# Fallback policy: DeepSeek primary degrades to DeterministicModelGateway on failure.
FALLBACK_POLICY_VERSION = "deepseek-to-deterministic-v1"
FALLBACK_ENABLED = True
RETRY_POLICY_VERSION = "transport-429-5xx-or-invalid-response-v1"
# Provider request/response contract used by ``DeepSeekModelGateway``: Chat
# Completions, JSON-object response format, thinking disabled, non-streaming. A
# behavior change to that contract requires a new version so the certified
# provider-config fingerprint changes even when endpoint/model/timeouts do not.
PROVIDER_RESPONSE_CONFIG_VERSION = "deepseek-chat-json-object-no-thinking-v1"

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
    artifact_bytes = _read_bounded_config_artifact(root, artifact_path)
    return verify_approval_evidence(approval, artifact_bytes=artifact_bytes)


def _read_bounded_config_artifact(root: Path, artifact_path: Any) -> bytes | None:
    """Read an artifact only when its resolved path remains inside ``config/``."""
    if not _within_config_boundary(artifact_path):
        return None
    try:
        config_root = (root / "config").resolve()
        full = (root / str(artifact_path)).resolve()
        if not full.is_relative_to(config_root) or not full.is_file():
            return None
        return full.read_bytes()
    except (OSError, RuntimeError):
        return None


def primary_provider_ready(settings: Any) -> bool:
    """Whether the CERTIFIED DeepSeek primary provider can actually be constructed.

    ``build_model_gateway`` only constructs the DeepSeek gateway when a credential is
    configured; otherwise production runs the deterministic fallback and the certified
    DeepSeek release is NOT runnable. Readiness is an independent runtime eligibility
    condition (NOT part of the signed rollout manifest), but activation must never
    authorize a non-zero rollout while the certified primary cannot be constructed.

    This never mutates the certified ``primary_provider``: the identity always describes
    the DeepSeek certified contract; readiness is reported separately.
    """
    return bool((getattr(settings, "deepseek_api_key", "") or "").strip())


def _normalized_base_url(settings: Any) -> str:
    """Canonical base URL: strip whitespace + trailing ``/`` (no aggressive URL
    normalization, so ``https://api.deepseek.com`` == ``https://api.deepseek.com/``)."""
    value = (getattr(settings, "deepseek_base_url", "") or "").strip()
    if value.endswith("/"):
        value = value.rstrip("/")
    return value


def effective_provider_config(settings: Any) -> dict[str, Any]:
    """Canonical NON-SECRET certified provider execution contract.

    Binds the certified model execution contract (NOT whichever provider happened to
    answer one request): the certified ``primary_provider``, normalized base_url, model,
    timeouts, bounded retry policy, AND the fallback/retry contract
    (``fallback_enabled`` + ``fallback_policy_version``). Only parameters that
    materially change certified provider behavior are included. API keys, authorization
    headers, the rollout salt, JWT and credentials are NEVER included.
    """
    return {
        "primary_provider": PRIMARY_PROVIDER,
        "base_url": _normalized_base_url(settings),
        "model": getattr(settings, "deepseek_model", "") or "",
        "connect_timeout_seconds": getattr(settings, "deepseek_connect_timeout_seconds", 0),
        "read_timeout_seconds": getattr(settings, "deepseek_read_timeout_seconds", 0),
        "total_timeout_seconds": getattr(settings, "deepseek_total_timeout_seconds", 0),
        "max_attempts": DEEPSEEK_MAX_ATTEMPTS,
        "retry_policy_version": RETRY_POLICY_VERSION,
        "fallback_enabled": FALLBACK_ENABLED,
        "fallback_policy_version": FALLBACK_POLICY_VERSION,
        "provider_response_config_version": PROVIDER_RESPONSE_CONFIG_VERSION,
    }


def provider_config_fingerprint(settings: Any) -> str:
    """SHA-256 of the canonical effective non-secret provider configuration."""
    payload = json.dumps(effective_provider_config(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
