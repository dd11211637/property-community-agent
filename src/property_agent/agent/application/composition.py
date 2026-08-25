"""Small PR4 runtime composition helpers kept out of the platform container."""

import logging
from typing import Any

from fastapi import FastAPI

from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.model_gateway import DeterministicModelGateway
from property_agent.agent.observed_boundaries import (
    ObservedPlanner,
    ObservedSpecialist,
    supervisor_observer,
)
from property_agent.agent.planning import SupervisorPlanner
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
    return AgentRuntimeFacadeImpl(
        lifecycle=lifecycle,
        conversations=conversations,
        policy=RuntimeSelectionPolicy(),
        v2_engine=_build_v2_engine(app),
    )


def bind_runtime(app, lifecycle, conversations, services) -> None:
    facade = build_runtime_facade(app, lifecycle=lifecycle, conversations=conversations)
    app.state.agent_lifecycle = lifecycle
    app.state.agent_runner = facade
    services["agent_runner"] = facade
    services["agent_lifecycle"] = lifecycle


def close_runtime_resources(app) -> None:
    resource = getattr(app.state, "langgraph_saver_resource", None)
    if resource is not None:
        resource.close()
    observability = getattr(app.state, "agent_observability", None)
    if observability is not None:
        observability.shutdown()
