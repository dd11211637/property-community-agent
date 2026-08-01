from fastapi import FastAPI

from property_agent.billing.adapters.api.router import router as billing_router
from property_agent.billing.application.service import BillingService
from property_agent.inspection.adapters.api.router import event_router, task_router
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.platform.http import install_http_foundation
from property_agent.repair.adapters.api.router import router as repair_router
from property_agent.repair.application.service import WorkOrderService


def create_app(
    *,
    repair_service: WorkOrderService | None = None,
    inspection_task_service: InspectionTaskService | None = None,
    security_event_service: SecurityEventService | None = None,
    billing_service: BillingService | None = None,
) -> FastAPI:
    """Create the project-level API and attach configured business services."""

    app = FastAPI(title="Property Community Agent API", version="0.1.0")
    app.state.work_order_service = repair_service
    app.state.task_service = inspection_task_service
    app.state.event_service = security_event_service
    app.state.billing_service = billing_service

    install_http_foundation(app)

    @app.get("/health", tags=["platform"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(repair_router)
    app.include_router(task_router)
    app.include_router(event_router)
    app.include_router(billing_router)
    return app

__all__ = ["create_app"]
