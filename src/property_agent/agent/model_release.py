"""Shared production model-release metadata contract (PR7-C model-release governance).

This is the SINGLE source of truth for the ACTUAL approved model/provider/prompt
release identity. It is consumed by three independent production paths so that a
rollout activation can be bound to the *real running* model/provider/prompt rather
than to a self-referential operator-supplied deployment string:

* production model composition (``build_model_gateway`` / ``build_rollout_control``);
* PR7-B certification metadata (``testing/pr7b/real_model_gate``);
* PR7-C rollout activation (``runtime_rollout_activation``).

These facts are server-owned and never operator-supplied. Do NOT redefine the
provider/prompt facts in ``testing/`` or operator env; import them from here. Do not
create a second model-configuration authority.

Approval governance: ``model_release_evidence_reference`` is NEVER a bare source
constant. It is derived by ``model_release_approval.verify_committed_baseline_approval``
from the protected real-model baseline approval artifact (manifest APPROVED + verified
artifact SHA-256). While the baseline is PENDING, the derived reference is empty and
any non-zero rollout fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass

# Canonical server-owned production provider configuration and prompt contract versions.
PROVIDER_CLASS = "deepseek"
PROVIDER_CONFIG_VERSION = "deepseek-bounded-retry-v1"
PROMPT_CONTRACT_VERSION = "semantic-planner-pr5-v1"


@dataclass(frozen=True, slots=True)
class ModelReleaseIdentity:
    """Bounded facts describing the ACTUAL running model/provider/prompt release.

    This is the authoritative running release identity. Rollout activation binds an
    approved manifest against these facts, never against operator env strings.

    ``model_release_evidence_reference`` is the verified approval evidence reference
    derived from the protected baseline approval artifact (empty while PENDING).
    ``provider_config_fingerprint`` is the SHA-256 of the canonical non-secret
    effective provider configuration (base_url / model / timeouts / retry contract).
    Neither field ever contains an API key, the rollout salt, or any credential.
    """

    provider_class: str
    model: str
    provider_config_version: str
    provider_config_fingerprint: str
    prompt_contract_version: str
    model_release_evidence_reference: str


def actual_model_release_identity() -> ModelReleaseIdentity:
    """The actual running model/provider/prompt release identity for this deployment.

    Derived from the real configured model (``settings.deepseek_model``), the ACTUAL
    running provider class (DeepSeek only when a credential is configured, otherwise
    the deterministic fallback), the canonical effective provider-config fingerprint,
    and the VERIFIED baseline approval evidence reference (empty while the protected
    real-model baseline approval is PENDING). The import of ``settings`` and the
    approval contract is lazy so this module stays importable from tests and
    certification metadata without a full application boot.
    """
    from property_agent.agent.model_release_approval import (
        actual_provider_class,
        provider_config_fingerprint,
        verify_committed_baseline_approval,
    )
    from property_agent.config import settings

    evidence = verify_committed_baseline_approval()
    return ModelReleaseIdentity(
        provider_class=actual_provider_class(settings),
        model=settings.deepseek_model,
        provider_config_version=PROVIDER_CONFIG_VERSION,
        provider_config_fingerprint=provider_config_fingerprint(settings),
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        model_release_evidence_reference=(
            evidence.evidence_reference if evidence is not None else ""
        ),
    )
