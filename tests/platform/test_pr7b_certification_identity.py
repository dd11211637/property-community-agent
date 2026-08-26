from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from property_agent.platform.adapters.api import health_routes
from property_agent.platform.adapters.api.dependencies import get_current_user
from property_agent.platform.adapters.api.health_routes import router


def test_certification_identity_is_authenticated_server_owned_and_bounded(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr(health_routes.settings, "deployment_environment", "preproduction")
    monkeypatch.setattr(health_routes.settings, "release_sha", "a" * 40)
    monkeypatch.setattr(health_routes.settings, "certification_write_enabled", True)

    response = TestClient(app).get(
        "/api/certification/identity", headers={"Authorization": "Bearer opaque"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "deployment_environment": "preproduction",
        "release_sha": "a" * 40,
        "certification_write_enabled": True,
    }


def test_certification_identity_rejects_unauthorized_request():
    app = FastAPI()
    app.include_router(router)

    def reject():
        raise HTTPException(401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = reject
    assert TestClient(app).get("/api/certification/identity").status_code == 401
