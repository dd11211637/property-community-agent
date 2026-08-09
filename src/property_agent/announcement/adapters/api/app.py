from fastapi import FastAPI

from property_agent.announcement.adapters.api.router import router
from property_agent.announcement.application.service import AnnouncementService
from property_agent.platform.http import install_http_foundation


def create_app(service: AnnouncementService | None = None) -> FastAPI:
    app = FastAPI(title="Property Community Announcement API", version="0.1.0")
    app.state.announcement_service = service
    install_http_foundation(app)
    app.include_router(router)
    return app
