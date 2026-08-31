"""Single-adapter bounded invocation boundary for typed capabilities."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from time import perf_counter
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
from property_agent.platform.domain.exceptions import PlatformError

ObservationHook = Callable[[str, dict[str, Any]], None]
logger = logging.getLogger(__name__)


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
        started = perf_counter()
        try:
            spec = self._registry.get(name)
        except UnknownCapabilityError as exc:
            return self._observed(
                name, self._failure(name, "UNKNOWN_CAPABILITY", str(exc), "registry"), started
            )
        name = spec.name
        try:
            request = spec.input_type.model_validate(payload)
        except ValidationError as exc:
            return self._observed(
                name,
                self._failure(
                    name,
                    "INVALID_CAPABILITY_INPUT",
                    "Capability input validation failed.",
                    "validation",
                    self._safe_validation_details(exc),
                ),
                started,
            )
        bounded = self._with_fingerprint(name, request, invocation)
        decision = self._policy.evaluate(spec, request, runtime, bounded)
        if decision.disposition != PolicyDisposition.ALLOW:
            return self._observed(name, self._policy_failure(name, decision), started)
        if (
            decision.approval_requirement == ApprovalRequirement.REQUIRED
            and not bounded.human_confirmed
        ):
            return self._observed(
                name,
                self._policy_failure(name, decision, code="HITL_CONFIRMATION_REQUIRED"),
                started,
            )
        adapter = self._adapters.get(name)
        if adapter is None:
            return self._observed(
                name,
                self._failure(name, "CAPABILITY_ADAPTER_MISSING", name, "configuration"),
                started,
            )
        self._observe_safely("capability_started", {"capability": name})
        try:
            raw_output = adapter(request, runtime)
        except Exception as exc:  # normalized public boundary; never retries or invokes twice
            logger.exception("Capability adapter failed", extra={"capability": name})
            result = self._adapter_failure(name, decision, bounded.fingerprint, exc)
            return self._observed(name, result, started)
        try:
            output = spec.output_type.model_validate(raw_output)
        except ValidationError as exc:
            result = self._output_failure(name, decision, bounded.fingerprint, exc)
            return self._observed(name, result, started)
        except Exception as exc:
            result = self._adapter_failure(name, decision, bounded.fingerprint, exc)
            return self._observed(name, result, started)
        return self._observed(
            name,
            CapabilityResult(name, decision, output=output, fingerprint=bounded.fingerprint),
            started,
        )

    def _observed(self, name: str, result: CapabilityResult, started: float) -> CapabilityResult:
        error = result.error
        outcome = "success"
        reason = None
        if error is not None:
            reason = error.code
            outcome = {
                "policy": "policy_denied",
                "business": "business_rejected",
                "validation": "schema_failure",
            }.get(error.kind, "infrastructure_failure")
        self._observe_safely(
            "capability_failed" if error is not None else "capability_finished",
            {
                "capability": name,
                "outcome": outcome,
                "reason": reason,
                "duration_seconds": perf_counter() - started,
            },
        )
        return result

    def _observe_safely(self, event: str, fields: dict[str, Any]) -> None:
        try:
            self._observe(event, fields)
        except Exception:
            return

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
        elif isinstance(exc, PlatformError):
            error = CapabilityError(
                exc.code,
                exc.message,
                "business",
                dict(getattr(exc, "details", None) or {}),
                exc,
            )
        else:
            error = CapabilityError(
                "CAPABILITY_EXECUTION_FAILED",
                "Capability execution failed.",
                "execution",
                {},
                exc,
            )
        return CapabilityResult(name, decision, error=error, fingerprint=fingerprint)

    @classmethod
    def _output_failure(cls, name, decision, fingerprint, exc: ValidationError) -> CapabilityResult:
        error = CapabilityError(
            "INVALID_CAPABILITY_OUTPUT",
            "Capability output validation failed.",
            "validation",
            cls._safe_validation_details(exc),
            exc,
        )
        return CapabilityResult(name, decision, error=error, fingerprint=fingerprint)

    @staticmethod
    def _safe_validation_details(exc: ValidationError) -> dict[str, Any]:
        return {
            "errors": [
                {
                    "type": str(error["type"]),
                    "loc": [str(part) for part in error["loc"]],
                }
                for error in exc.errors(include_url=False)
            ]
        }
