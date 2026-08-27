"""Shared production model-release metadata contract (PR7-C Blocker 1).

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
"""

from __future__ import annotations

from dataclasses import dataclass

# Canonical server-owned production provider configuration and prompt contract versions.
PROVIDER_CLASS = "deepseek"
PROVIDER_CONFIG_VERSION = "deepseek-bounded-retry-v1"
PROMPT_CONTRACT_VERSION = "semantic-planner-pr5-v1"

# The real approved model/release evidence reference required for any non-zero rollout.
# It stays ``PENDING`` until the protected real-model baseline approval (PR7-B) records
# a real bounded reference here; a non-zero rollout therefore remains fail-closed.
REAL_MODEL_RELEASE_EVIDENCE_REFERENCE = "PENDING"


@dataclass(frozen=True, slots=True)
class ModelReleaseIdentity:
    """Bounded facts describing the ACTUAL running model/provider/prompt release.

    This is the authoritative running release identity. Rollout activation binds an
    approved manifest against these facts, never against operator env strings.
    """

    provider_class: str
    model: str
    provider_config_version: str
    prompt_contract_version: str
    model_release_evidence_reference: str


def actual_model_release_identity() -> ModelReleaseIdentity:
    """The actual running model/provider/prompt release identity for this deployment.

    Derived from the real configured model (``settings.deepseek_model``) and the
    canonical server-owned provider/prompt facts. The import of ``settings`` is lazy
    so this module stays importable from tests and certification metadata without a
    full application boot.
    """
    from property_agent.config import settings

    return ModelReleaseIdentity(
        provider_class=PROVIDER_CLASS,
        model=settings.deepseek_model,
        provider_config_version=PROVIDER_CONFIG_VERSION,
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        model_release_evidence_reference=REAL_MODEL_RELEASE_EVIDENCE_REFERENCE,
    )
