from fastapi import FastAPI

from property_agent.inspection.adapters.api.router import event_router, task_router
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.platform.http import install_http_foundation


def create_app(
    task_service: InspectionTaskService | None = None,
    event_service: SecurityEventService | None = None,
) -> FastAPI:
    app = FastAPI(title="Property Community Inspection API", version="0.1.0")
    app.state.task_service = task_service
    app.state.event_service = event_service

    install_http_foundation(app)
    app.include_router(task_router)
    app.include_router(event_router)
    return app
