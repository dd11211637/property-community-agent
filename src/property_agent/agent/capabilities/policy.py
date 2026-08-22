"""Deterministic invocation-specific orchestration classification."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from property_agent.agent.capabilities.contracts import (
    ApprovalPosture,
    ApprovalRequirement,
    CapabilityInput,
    CapabilityInvocationState,
    CapabilityPolicyDecision,
    CapabilityRuntimeContext,
    CapabilitySpec,
    PolicyDisposition,
)

ClassificationRule = Callable[
    [CapabilitySpec, CapabilityInput, CapabilityRuntimeContext, CapabilityInvocationState],
    CapabilityPolicyDecision | None,
]


class CapabilityPolicy:
    """Classify an attempt without replacing live Application Service authority."""

    def __init__(self, rules: Mapping[str, ClassificationRule] | None = None) -> None:
        self._rules = dict(rules or {})

    def evaluate(
        self,
        spec: CapabilitySpec,
        request: CapabilityInput,
        runtime: CapabilityRuntimeContext,
        invocation: CapabilityInvocationState,
    ) -> CapabilityPolicyDecision:
        denied = self._bounded_denial(spec, runtime, invocation)
        if denied is not None:
            return denied
        rule = self._rules.get(spec.name)
        classified = rule(spec, request, runtime, invocation) if rule else None
        if classified is not None:
            return classified
        if spec.approval_posture == ApprovalPosture.HUMAN_ONLY:
            return CapabilityPolicyDecision(
                PolicyDisposition.HUMAN_ONLY,
                spec.baseline_risk,
                ApprovalRequirement.REQUIRED,
                "CAPABILITY_HUMAN_ONLY",
            )
        approval = self._effective_approval(spec)
        return CapabilityPolicyDecision(
            PolicyDisposition.ALLOW,
            spec.baseline_risk,
            approval,
            "POLICY_ALLOW",
        )

    @staticmethod
    def _effective_approval(spec: CapabilitySpec) -> ApprovalRequirement:
        if spec.approval_posture == ApprovalPosture.NONE:
            return ApprovalRequirement.NONE
        return ApprovalRequirement.REQUIRED

    @staticmethod
    def _bounded_denial(
        spec: CapabilitySpec,
        runtime: CapabilityRuntimeContext,
        invocation: CapabilityInvocationState,
    ) -> CapabilityPolicyDecision | None:
        reason = None
        if runtime.request_context is None:
            reason = "TRUSTED_CONTEXT_REQUIRED"
        elif invocation.allowlist is not None and spec.name not in invocation.allowlist:
            reason = "CAPABILITY_NOT_ALLOWLISTED"
        elif invocation.step >= invocation.max_steps:
            reason = "MAX_STEPS_EXCEEDED"
        elif invocation.calls_made >= invocation.max_calls:
            reason = "EXECUTION_BUDGET_EXCEEDED"
        elif (
            invocation.deadline_monotonic is not None
            and time.monotonic() >= invocation.deadline_monotonic
        ):
            reason = "EXECUTION_DEADLINE_EXCEEDED"
        elif invocation.fingerprint in invocation.prior_fingerprints:
            reason = "DUPLICATE_INVOCATION"
        if reason is None:
            return None
        return CapabilityPolicyDecision(
            PolicyDisposition.DENY,
            spec.baseline_risk,
            ApprovalRequirement.NONE,
            reason,
        )
