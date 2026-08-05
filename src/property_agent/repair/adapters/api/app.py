from fastapi import FastAPI

from property_agent.platform.http import install_http_foundation
from property_agent.repair.adapters.api.router import router
from property_agent.repair.application.service import WorkOrderService


def create_app(service: WorkOrderService | None = None) -> FastAPI:
    app = FastAPI(title="Property Community Repair API", version="0.1.0")
    if service is not None:
        app.state.work_order_service = service

    install_http_foundation(app)
    app.include_router(router)
    return app
