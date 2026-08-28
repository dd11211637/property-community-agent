"""Small PR4 runtime composition helpers kept out of the platform container."""

import logging
from typing import Any

from fastapi import FastAPI

from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.approval_authority import configured_approval_authority
from property_agent.agent.model_gateway import DeterministicModelGateway
from property_agent.agent.model_release import actual_model_release_identity
from property_agent.agent.observed_boundaries import (
    ObservedPlanner,
    ObservedSpecialist,
    supervisor_observer,
)
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.runtime_rollout import (
    RolloutConfig,
    RolloutControl,
    RuntimeEligibility,
    activate_rollout_control,
    load_rollout_activation_manifest,
)
from property_agent.agent.runtime_version import RuntimeSelectionPolicy
from property_agent.agent.specialists import (
    AnnouncementSpecialist,
    BillingSpecialist,
    InspectionSpecialist,
    RepairSpecialist,
)
from property_agent.agent.specialists.supervisor import Supervisor
from property_agent.config import settings

logger = logging.getLogger(__name__)


def build_supervisor(app: FastAPI) -> Supervisor:
    """Assemble the four canonical stateless specialists around one executor."""
    executor = app.state.agent_capability_executor
    gateway = getattr(app.state, "agent_model_gateway", DeterministicModelGateway())
    specialists = tuple(
        ObservedSpecialist(specialist, app.state.agent_observability)
        for specialist in (
            RepairSpecialist(executor),
            BillingSpecialist(executor),
            AnnouncementSpecialist(executor),
            InspectionSpecialist(executor),
        )
    )
    planner = SupervisorPlanner(
        gateway, memory_reader=getattr(app.state, "agent_memory_reader", None)
    )
    return Supervisor(
        ObservedPlanner(planner, app.state.agent_observability),
        {specialist.name: specialist for specialist in specialists},
        observe=supervisor_observer(app.state.agent_observability),
    )


def _build_v2_engine(app: FastAPI) -> Any | None:
    try:
        from property_agent.agent.application.langgraph_runtime import (
            LangGraphEngine,
            build_saver_resource,
        )

        is_sqlite = settings.database_url.strip().lower().startswith("sqlite")
        dsn = None
        if not is_sqlite:
            dsn = settings.database_url.replace("postgresql+psycopg", "postgresql")
        resource = build_saver_resource(
            in_memory=is_sqlite,
            dsn=dsn,
            observability=app.state.agent_observability,
        )
        app.state.langgraph_saver_resource = resource
        return LangGraphEngine(resource.saver, build_supervisor(app))
    except Exception:
        logger.exception("v2 LangGraph engine unavailable; v1 remains the public runtime")
        return None


def build_runtime_facade(
    app: FastAPI,
    *,
    lifecycle: AgentSessionRunner,
    conversations: ConversationService,
):
    v2_engine = _build_v2_engine(app)
    policy = _build_runtime_policy(app, v2_engine_available=v2_engine is not None)
    app.state.agent_runtime_policy = policy
    return AgentRuntimeFacadeImpl(
        lifecycle=lifecycle,
        conversations=conversations,
        policy=policy,
        v2_engine=v2_engine,
    )


def build_rollout_control_from_settings(
    settings,
    *,
    manifest,
    audit_sink=None,
    model_release_identity=None,
) -> RolloutControl:
    """Real production activation boundary built from validated settings.

    Delegates to ``activate_rollout_control`` so a non-zero rollout can only become
    active when an ``APPROVED`` activation manifest matches the deployed release AND
    the certified model execution identity and current provider readiness (Blocker 1). This is the
    single place configuration turns into an active rollout control.

    The model/prompt approval identity is derived from the shared production
    ``ModelReleaseIdentity`` (server-owned), never from operator env, so a deployment
    cannot self-approve a rollout by supplying matching-looking operator strings.
    """
    actual = model_release_identity or actual_model_release_identity()
    config = RolloutConfig(
        basis_points=settings.agent_v2_new_conversation_rollout_basis_points,
        secret_salt=settings.agent_v2_rollout_salt.encode(),
        salt_version=settings.agent_v2_rollout_salt_version,
        config_version=settings.agent_v2_rollout_config_version,
        eligibility_policy_version=settings.agent_v2_eligibility_policy_version,
        fallback_runtime=settings.agent_v2_new_conversation_fallback_runtime,
        model_approval_id=actual.model_release_evidence_reference,
        prompt_contract_version=actual.prompt_contract_version,
    )
    return activate_rollout_control(
        config,
        release_sha=settings.release_sha or None,
        manifest=manifest,
        audit_sink=audit_sink,
        model_release_identity=actual,
        approval_authority=configured_approval_authority(settings),
    )


def _build_runtime_policy(app: FastAPI, *, v2_engine_available: bool) -> RuntimeSelectionPolicy:
    manifest = load_rollout_activation_manifest(settings.rollout_activation_manifest_path)
    observability = getattr(app.state, "agent_observability", None)
    audit_sink = observability.observe_rollout_audit_event if observability is not None else None
    control = build_rollout_control_from_settings(
        settings, manifest=manifest, audit_sink=audit_sink
    )
    eligibility = RuntimeEligibility(
        deployment_compatible=settings.deployment_environment
        in {"isolated-test", "preproduction", "production"},
        v2_engine_available=v2_engine_available,
        official_saver_available=v2_engine_available,
        accepted_head_available=False,
        model_config_approved=settings.agent_v2_model_config_approved,
        emergency_stop=settings.agent_v2_emergency_stop,
    )
    return RuntimeSelectionPolicy(
        control=control,
        eligibility=eligibility,
        assignment_observer=(
            observability.observe_runtime_assignment if observability is not None else None
        ),
    )


def bind_runtime(app, lifecycle, conversations, services) -> None:
    facade = build_runtime_facade(app, lifecycle=lifecycle, conversations=conversations)
    app.state.agent_lifecycle = lifecycle
    app.state.agent_runner = facade
    app.state.agent_conversations = conversations
    services["agent_runner"] = facade
    services["agent_lifecycle"] = lifecycle


def close_runtime_resources(app) -> None:
    resource = getattr(app.state, "langgraph_saver_resource", None)
    if resource is not None:
        resource.close()
    observability = getattr(app.state, "agent_observability", None)
    if observability is not None:
        observability.shutdown()
