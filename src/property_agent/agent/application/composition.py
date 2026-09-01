"""Production composition for the V2-only LangGraph Supervisor runtime."""

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.model_gateway import DeterministicModelGateway
from property_agent.agent.observed_boundaries import (
    ObservedPlanner,
    ObservedSpecialist,
    supervisor_observer,
)
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.specialists import (
    AnnouncementSpecialist,
    BillingSpecialist,
    InspectionSpecialist,
    RepairSpecialist,
)
from property_agent.agent.specialists.supervisor import Supervisor
from property_agent.config import settings


@dataclass
class V2RuntimeReadiness:
    """Compatibility-shaped readiness for a service with no runtime selector."""

    accepted_head_available: bool = False

    def observe_accepted_head(self, *, available: bool) -> None:
        self.accepted_head_available = available

    def readiness(self) -> dict[str, str | int | bool]:
        return {
            "state": "V2_ONLY" if self.accepted_head_available else "NOT_READY",
            "ready": self.accepted_head_available,
            "rollout_basis_points": 10_000,
            "config_version": "v2-only-v1",
            "salt_version": "not-applicable",
            "eligibility_policy_version": "v2-only-v1",
            "fallback_runtime": "v2",
            "reason": "v2_only" if self.accepted_head_available else "accepted_head_unavailable",
        }


def build_supervisor(app: FastAPI) -> Supervisor:
    """Assemble the four canonical stateless specialists around one executor."""
    executor = app.state.agent_capability_executor
    gateway = getattr(app.state, "agent_model_gateway", None)
    if gateway is None:
        if not settings.env.strip().lower().startswith("test"):
            raise RuntimeError("V2 Supervisor model gateway is not configured")
        gateway = DeterministicModelGateway()
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
        react_gateway=gateway,
    )


def build_v2_engine(app: FastAPI) -> Any:
    """Build the mandatory official LangGraph engine or fail application startup."""
    from property_agent.agent.application.langgraph_runtime import (
        LangGraphEngine,
        build_saver_resource,
    )

    is_unit_test = settings.env.strip().lower() == "test"
    is_sqlite = settings.database_url.strip().lower().startswith("sqlite")
    use_in_memory_saver = is_unit_test or is_sqlite
    dsn = None
    if not use_in_memory_saver:
        dsn = settings.database_url.replace("postgresql+psycopg", "postgresql")
    resource = build_saver_resource(
        in_memory=use_in_memory_saver,
        dsn=dsn,
        observability=app.state.agent_observability,
    )
    app.state.langgraph_saver_resource = resource
    return LangGraphEngine(resource.saver, build_supervisor(app))


def bind_runtime(app, lifecycle: AgentSessionRunner, conversations, services) -> None:
    facade = AgentRuntimeFacadeImpl(lifecycle=lifecycle)
    app.state.agent_runtime_policy = V2RuntimeReadiness()
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
