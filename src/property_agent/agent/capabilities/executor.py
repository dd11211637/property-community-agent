"""Bounded, exactly-once-invocation boundary for typed capabilities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from property_agent.agent.capabilities.contracts import (
    ApprovalRequirement,
    CapabilityAdapter,
    CapabilityDomainError,
    CapabilityError,
    CapabilityInput,
    CapabilityInvocationState,
    CapabilityResult,
    CapabilityRuntimeContext,
    PolicyDisposition,
)
from property_agent.agent.capabilities.policy import CapabilityPolicy
from property_agent.agent.capabilities.registry import CapabilityRegistry, UnknownCapabilityError
from property_agent.platform.application.hashing import canonical_hash

ObservationHook = Callable[[str, dict[str, Any]], None]


class CapabilityExecutor:
    def __init__(
        self,
        registry: CapabilityRegistry,
        policy: CapabilityPolicy,
        adapters: Mapping[str, CapabilityAdapter],
        *,
        observe: ObservationHook | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._adapters = dict(adapters)
        self._observe = observe or (lambda _event, _fields: None)

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry

    def execute(
        self,
        name: str,
        payload: Mapping[str, Any] | CapabilityInput,
        runtime: CapabilityRuntimeContext,
        invocation: CapabilityInvocationState | None = None,
    ) -> CapabilityResult:
        try:
            spec = self._registry.get(name)
        except UnknownCapabilityError as exc:
            return self._failure(name, "UNKNOWN_CAPABILITY", str(exc), "registry")
        try:
            request = spec.input_type.model_validate(payload)
        except ValidationError as exc:
            return self._failure(
                name,
                "INVALID_CAPABILITY_INPUT",
                "Capability input validation failed.",
                "validation",
                {"errors": exc.errors(include_url=False)},
            )
        bounded = self._with_fingerprint(name, request, invocation)
        decision = self._policy.evaluate(spec, request, runtime, bounded)
        if decision.disposition != PolicyDisposition.ALLOW:
            return self._policy_failure(name, decision)
        if (
            decision.approval_requirement == ApprovalRequirement.REQUIRED
            and not bounded.human_confirmed
        ):
            return self._policy_failure(name, decision, code="HITL_CONFIRMATION_REQUIRED")
        adapter = self._adapters.get(name)
        if adapter is None:
            return self._failure(name, "CAPABILITY_ADAPTER_MISSING", name, "configuration")
        self._observe("capability_started", {"capability": name})
        try:
            raw_output = adapter(request, runtime)
            output = spec.output_type.model_validate(raw_output)
        except Exception as exc:  # normalized public boundary; never retries or invokes twice
            result = self._adapter_failure(name, decision, bounded.fingerprint, exc)
            self._observe("capability_failed", {"capability": name, "code": result.error.code})
            return result
        self._observe("capability_finished", {"capability": name, "ok": True})
        return CapabilityResult(name, decision, output=output, fingerprint=bounded.fingerprint)

    @staticmethod
    def _with_fingerprint(
        name: str,
        request: CapabilityInput,
        invocation: CapabilityInvocationState | None,
    ) -> CapabilityInvocationState:
        state = invocation or CapabilityInvocationState()
        fingerprint = canonical_hash({"capability": name, "input": request.model_dump(mode="json")})
        return replace(state, fingerprint=fingerprint)

    @staticmethod
    def _policy_failure(name, decision, *, code: str | None = None) -> CapabilityResult:
        error_code = code or decision.reason_code
        error = CapabilityError(error_code, decision.reason_code, "policy")
        return CapabilityResult(name, decision, error=error)

    @staticmethod
    def _failure(
        name: str,
        code: str,
        message: str,
        kind: str,
        details: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        return CapabilityResult(
            name,
            None,
            error=CapabilityError(code, message, kind, details or {}),
        )

    @staticmethod
    def _adapter_failure(name, decision, fingerprint, exc: Exception) -> CapabilityResult:
        if isinstance(exc, CapabilityDomainError):
            error = CapabilityError(exc.code, exc.message, "business", exc.details, exc)
        elif isinstance(exc, ValidationError):
            error = CapabilityError(
                "INVALID_CAPABILITY_OUTPUT",
                "Capability output validation failed.",
                "validation",
                {"errors": exc.errors(include_url=False)},
                exc,
            )
        else:
            error = CapabilityError(
                str(getattr(exc, "code", "CAPABILITY_EXECUTION_FAILED")),
                str(getattr(exc, "message", exc)),
                "business" if hasattr(exc, "code") else "execution",
                dict(getattr(exc, "details", None) or {}),
                exc,
            )
        return CapabilityResult(name, decision, error=error, fingerprint=fingerprint)
