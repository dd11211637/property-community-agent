"""Shared stateless specialist invocation boundary."""

from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

from property_agent.agent.capabilities.contracts import (
    CapabilityRuntimeContext,
    CapabilityWriteContext,
)
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.orchestration import (
    PlanStep,
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.specialists.presentation import present_success
from property_agent.agent.state import AgentState
from property_agent.inspection.adapters.api.dependencies import to_inspection_context
from property_agent.platform.application.hashing import canonical_hash


class StatelessSpecialist:
    """Own domain interpretation, never persistence or business authority."""

    name: ClassVar[SpecialistName]
    domain: ClassVar[str]

    def __init__(self, executor: CapabilityExecutor) -> None:
        self._executor = executor
        specs = tuple(spec for spec in executor.registry.inventory() if spec.domain == self.domain)
        self.allowlist = frozenset(spec.name for spec in specs)
        self.capability_inventory = tuple(_capability_summary(spec) for spec in specs)
        self._input_types = {spec.name: spec.input_type for spec in specs}

    def arguments_valid(self, capability: str, arguments: dict) -> bool:
        input_type = self._input_types.get(capability)
        if input_type is None:
            return False
        try:
            input_type.model_validate(arguments)
        except ValueError:
            return False
        return True

    def invoke(
        self,
        step: PlanStep,
        state: AgentState,
        runtime: RuntimeContext,
        prior_results: tuple[SpecialistResult, ...],
    ) -> SpecialistResult:
        capability = step.capability or self.choose_capability(step, state, prior_results)
        if step.domain != self.domain or step.specialist != self.name:
            return self._unsupported(step, capability, "SPECIALIST_DOMAIN_MISMATCH")
        if capability not in self.allowlist:
            return self._unsupported(step, capability, "CAPABILITY_NOT_IN_SPECIALIST_ALLOWLIST")
        parameters = self.project_parameters(capability, step, state, prior_results)
        params_hash = canonical_hash(parameters)
        confirmed = self._confirmed(runtime, state, step, capability, params_hash)
        invocation = replace(
            state.capability_invocation,
            selected_capability=capability,
            human_confirmed=confirmed,
        )
        result = self._executor.execute(
            capability,
            parameters,
            self._capability_runtime(runtime, state, confirmed),
            invocation,
        )
        if not result.ok:
            return self.interpret_error(step, capability, parameters, params_hash, result)
        data = result.output.model_dump(mode="json")
        nested = data.get("data")
        if len(data) == 1 and isinstance(nested, dict):
            data = nested
        return SpecialistResult(
            SpecialistOutcome.SUCCESS,
            step.step_id,
            self.name,
            capability=capability,
            data=data,
            public_message=self.success_message(capability, data),
            fingerprint=result.fingerprint,
        )

    def choose_capability(self, step, state, prior_results) -> str:
        raise NotImplementedError

    def project_parameters(self, capability, step, state, prior_results):
        del capability, state, prior_results
        return dict(step.parameters)

    def interpret_error(self, step, capability, parameters, params_hash, result):
        error = result.error
        code = error.code if error else "CAPABILITY_EXECUTION_FAILED"
        if code == "HITL_CONFIRMATION_REQUIRED":
            return SpecialistResult(
                SpecialistOutcome.HITL_REQUIRED,
                step.step_id,
                self.name,
                capability=capability,
                data={
                    "parameters": parameters,
                    "params_hash": params_hash,
                    "operation_level": result.decision.effective_risk.value,
                },
                public_message="该操作需要您确认后才能执行。",
                reason_code=code,
                fingerprint=result.fingerprint,
            )
        if result.decision and result.decision.disposition.value == "human-only":
            return SpecialistResult(
                SpecialistOutcome.HANDOVER,
                step.step_id,
                self.name,
                capability=capability,
                public_message="该操作只能由授权人工处理。",
                reason_code=code,
            )
        if code == "INVALID_CAPABILITY_INPUT":
            missing = tuple(
                str(item.get("loc", ["input"])[-1]) for item in error.details.get("errors", [])
            )
            return SpecialistResult(
                SpecialistOutcome.NEEDS_CLARIFICATION,
                step.step_id,
                self.name,
                capability=capability,
                public_message="执行该步骤还需要补充信息。",
                reason_code=code,
                missing_inputs=missing,
            )
        return SpecialistResult(
            SpecialistOutcome.CAPABILITY_ERROR,
            step.step_id,
            self.name,
            capability=capability,
            public_message=error.message if error else "操作失败。",
            reason_code=code,
            fingerprint=result.fingerprint,
        )

    def success_message(self, capability, data) -> str:
        return present_success(capability, data)

    @staticmethod
    def _confirmed(runtime, state, step, capability, params_hash) -> bool:
        prepared = runtime.prepared_write
        plan_id = getattr(state.plan, "plan_id", None)
        goal_id = step.step_id if state.plan is None else None
        return bool(
            prepared
            and prepared.matches(
                capability=capability,
                params_hash=params_hash,
                plan_id=plan_id,
                plan_step_id=step.step_id if state.plan is not None else None,
                goal_id=goal_id,
            )
        )

    @staticmethod
    def _capability_runtime(runtime, state, confirmed):
        prepared = runtime.prepared_write if confirmed else None
        write = (
            CapabilityWriteContext(
                prepared.confirmation_token,
                prepared.idempotency_key,
                prepared.approval_ref,
            )
            if prepared is not None
            else None
        )
        return CapabilityRuntimeContext(
            request_context=runtime.request_context,
            current_house_id=runtime.current_house_id,
            legacy_state=state,
            write=write,
            trusted_runtime=runtime,
            inspection_context_projector=lambda context: to_inspection_context(
                context, context.request_id
            ),
        )

    def _unsupported(self, step, capability, code):
        return SpecialistResult(
            SpecialistOutcome.UNSUPPORTED,
            step.step_id,
            self.name,
            capability=capability,
            public_message="该领域步骤不受支持。",
            reason_code=code,
        )


def _capability_summary(spec):
    fields = spec.input_type.model_fields
    return {
        "name": spec.name,
        "purpose": spec.presentation.title,
        "risk": spec.baseline_risk.value,
        "approval_posture": spec.approval_posture.value,
        "required_inputs": sorted(name for name, field in fields.items() if field.is_required()),
        "optional_inputs": sorted(
            name for name, field in fields.items() if not field.is_required()
        ),
        "input_schema": spec.input_type.model_json_schema().get("properties", {}),
    }
