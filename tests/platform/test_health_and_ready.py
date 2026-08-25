"""
Tests for cloud-native health check probes — PRD 5.4.

Covers:
- GET /health  → 200 {"status": "UP"}
- GET /ready   → 200 when database UP + services configured
- GET /ready   → 503 when database DOWN
- GET /ready   → 503 when services not configured
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import property_agent.platform.container as container_module
from property_agent.platform.adapters.api.health_routes import router as health_router
from property_agent.platform.container import build_production_container

# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_container_state():
    """Reset global container state before each test."""
    container_module._services_configured = False
    container_module._async_engine = None
    container_module._async_session_factory = None
    with patch(
        "property_agent.platform.adapters.api.health_routes.check_accepted_head_store",
        new_callable=AsyncMock,
        return_value=True,
    ):
        yield
    container_module._services_configured = False
    container_module._async_engine = None
    container_module._async_session_factory = None


@pytest.fixture
def app() -> FastAPI:
    """Minimal FastAPI app with health router mounted."""
    app = FastAPI()
    app.include_router(health_router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════
# GET /health — Liveness Probe
# ═══════════════════════════════════════════════════════════════


class TestHealthLiveness:
    """Tests for GET /health liveness probe."""

    def test_health_returns_200(self, client: TestClient):
        """GET /health should return 200 with status UP."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "UP"}

    def test_health_is_fast(self, client: TestClient):
        """GET /health should respond quickly (no DB/network calls)."""
        response = client.get("/health")
        assert response.elapsed.total_seconds() < 1.0

    def test_health_idempotent(self, client: TestClient):
        """Multiple calls to /health should return the same result."""
        r1 = client.get("/health")
        r2 = client.get("/health")
        r3 = client.get("/health")
        assert r1.json() == r2.json() == r3.json() == {"status": "UP"}


# ═══════════════════════════════════════════════════════════════
# GET /ready — Readiness Probe (happy path)
# ═══════════════════════════════════════════════════════════════


class TestReadyReadinessSuccess:
    """Tests for GET /ready when database is UP and services are configured."""

    def test_ready_returns_200_when_all_ready(self, app: FastAPI):
        """GET /ready should return 200 when database and services are ready."""
        # Build the production container to mark services as configured
        build_production_container(app)

        # Mock check_database_health to return True
        with patch(
            "property_agent.platform.adapters.api.health_routes.check_database_health",
            new_callable=AsyncMock,
        ) as mock_db:
            mock_db.return_value = True

            client = TestClient(app)
            response = client.get("/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "READY"
        assert data["components"]["database"] == "UP"
        assert data["components"]["services"] == "UP"
        assert data["components"]["stream_execution"]["state"] == "ACCEPTING"
        assert data["components"]["stream_execution"]["active"] == 0
        assert data["components"]["memory_embedding"]["state"] in {
            "DISABLED",
            "CONFIGURED_UNKNOWN",
        }
        assert data["components"]["memory_writer"]["state"] in {
            "DISABLED",
            "CONFIGURED_UNKNOWN",
        }

    def test_ready_response_structure(self, app: FastAPI):
        """GET /ready response should have the correct structure."""
        build_production_container(app)

        with patch(
            "property_agent.platform.adapters.api.health_routes.check_database_health",
            new_callable=AsyncMock,
        ) as mock_db:
            mock_db.return_value = True

            client = TestClient(app)
            response = client.get("/ready")

        data = response.json()
        assert "status" in data
        assert "components" in data
        assert "database" in data["components"]
        assert "services" in data["components"]


# ═══════════════════════════════════════════════════════════════
# GET /ready — Readiness Probe (failure cases)
# ═══════════════════════════════════════════════════════════════


class TestReadyReadinessFailure:
    """Tests for GET /ready when components are unhealthy."""

    def test_ready_returns_503_when_database_down(self, app: FastAPI):
        """GET /ready should return 503 when database is unreachable."""
        build_production_container(app)

        with patch(
            "property_agent.platform.adapters.api.health_routes.check_database_health",
            new_callable=AsyncMock,
        ) as mock_db:
            mock_db.return_value = False

            client = TestClient(app)
            response = client.get("/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["status"] == "NOT_READY"
        assert data["detail"]["components"]["database"] == "DOWN"
        assert data["detail"]["components"]["services"] == "UP"

    def test_ready_returns_503_when_services_unconfigured(self, app: FastAPI):
        """GET /ready should return 503 when services are not assembled."""
        # Do NOT call build_production_container — services are unconfigured

        with patch(
            "property_agent.platform.adapters.api.health_routes.check_database_health",
            new_callable=AsyncMock,
        ) as mock_db:
            mock_db.return_value = True

            client = TestClient(app)
            response = client.get("/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["status"] == "NOT_READY"
        assert data["detail"]["components"]["database"] == "UP"
        assert data["detail"]["components"]["services"] == "UNCONFIGURED"

    def test_ready_returns_503_when_accepted_head_schema_is_unavailable(self, app: FastAPI):
        build_production_container(app)
        with (
            patch(
                "property_agent.platform.adapters.api.health_routes.check_database_health",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "property_agent.platform.adapters.api.health_routes.check_accepted_head_store",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            response = TestClient(app).get("/ready")

        assert response.status_code == 503
        assert response.json()["detail"]["components"]["accepted_head_store"] == "DOWN"

    def test_ready_returns_503_when_both_down(self, app: FastAPI):
        """GET /ready should return 503 when both database and services are down."""
        # Do NOT call build_production_container

        with patch(
            "property_agent.platform.adapters.api.health_routes.check_database_health",
            new_callable=AsyncMock,
        ) as mock_db:
            mock_db.return_value = False

            client = TestClient(app)
            response = client.get("/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["status"] == "NOT_READY"
        assert data["detail"]["components"]["database"] == "DOWN"
        assert data["detail"]["components"]["services"] == "UNCONFIGURED"

    def test_ready_returns_correct_status_code_on_failure(self, app: FastAPI):
        """503 response should explicitly be 503, not 200."""
        # Neither DB nor services are ready
        with patch(
            "property_agent.platform.adapters.api.health_routes.check_database_health",
            new_callable=AsyncMock,
        ) as mock_db:
            mock_db.return_value = False

            client = TestClient(app)
            response = client.get("/ready")

        # Status code must be 503
        assert response.status_code == 503
        # The response body should NOT contain status "READY"
        assert response.json()["detail"]["status"] != "READY"


# ═══════════════════════════════════════════════════════════════
# Integration: Health + Ready co-existence
# ═══════════════════════════════════════════════════════════════


class TestHealthAndReadyIntegration:
    """Verify that /health and /ready work independently."""

    def test_health_works_even_when_ready_fails(self, app: FastAPI):
        """/health should return 200 even when /ready returns 503."""
        with patch(
            "property_agent.platform.adapters.api.health_routes.check_database_health",
            new_callable=AsyncMock,
        ) as mock_db:
            mock_db.return_value = False

            client = TestClient(app)

            health_resp = client.get("/health")
            ready_resp = client.get("/ready")

        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "UP"}
        assert ready_resp.status_code == 503
