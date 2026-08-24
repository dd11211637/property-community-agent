"""Small PR4 runtime composition helpers kept out of the platform container."""

import logging
from typing import Any

from fastapi import FastAPI

from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.facade import AgentRuntimeFacadeImpl
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.runtime_version import RuntimeSelectionPolicy
from property_agent.config import settings

logger = logging.getLogger(__name__)


def _build_v2_engine(app: FastAPI) -> Any | None:
    try:
        from property_agent.agent.application.langgraph_runtime import (
            LangGraphEngine,
            build_saver_resource,
        )
        from property_agent.agent.specialists.repair import RepairPilotSpecialist

        is_sqlite = settings.database_url.strip().lower().startswith("sqlite")
        dsn = None
        if not is_sqlite:
            dsn = settings.database_url.replace("postgresql+psycopg", "postgresql")
        resource = build_saver_resource(in_memory=is_sqlite, dsn=dsn)
        app.state.langgraph_saver_resource = resource
        specialist = RepairPilotSpecialist(app.state.agent_capability_executor)
        return LangGraphEngine(resource.saver, specialist)
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
