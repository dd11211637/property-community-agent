"""
Cloud-native health check probes — PRD 5.4.

Provides:
- GET /health  — Liveness probe (process alive, no external checks)
- GET /ready   — Readiness probe (database + services verified)

Response format follows Kubernetes probe conventions:
- /health:  200 {"status": "UP"}
- /ready:   200 {"status": "READY", "components": {...}}
            503 {"status": "NOT_READY", "components": {...}}"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from property_agent.platform.container import (
    are_services_configured,
    check_database_health,
)
from property_agent.platform.readiness import check_accepted_head_store

router = APIRouter(tags=["health"])


# ═══════════════════════════════════════════════════════════════
# GET /health — Liveness Probe
# ═══════════════════════════════════════════════════════════════


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — reports process status only.

    Always returns 200 as long as the process is alive. No database
    queries or external network calls are performed.
    """
    return {"status": "UP"}


# ═══════════════════════════════════════════════════════════════
# GET /ready — Readiness Probe
# ═══════════════════════════════════════════════════════════════


@router.get("/ready")
async def ready(request: Request) -> dict:
    """Readiness probe — validates database connectivity and service assembly.

    Checks:
    1. Database: runs SELECT 1 against the async connection pool.
    2. Services: verifies the production container has been assembled.

    Returns:
        200 {"status": "READY", "components": {"database": "UP", "services": "UP"}}
        503 {"status": "NOT_READY", "components": {"database": "DOWN", "services": "UNCONFIGURED"}}
    """
    db_up = await check_database_health()
    accepted_head_up = await check_accepted_head_store()
    svc_up = are_services_configured()

    components = {
        "database": "UP" if db_up else "DOWN",
        "services": "UP" if svc_up else "UNCONFIGURED",
        "accepted_head_store": "UP" if accepted_head_up else "DOWN",
        "telemetry": _telemetry_status(request),
        "stream_execution": _stream_execution_status(request),
        "database_pool": _database_pool_status(request),
        "memory_embedding": _optional_component(request, "agent_memory_embedding_provider"),
        "memory_writer": _optional_component(request, "agent_memory_writer"),
    }

    all_ready = db_up and accepted_head_up and svc_up

    if all_ready:
        return {"status": "READY", "components": components}

    raise HTTPException(
        status_code=503,
        detail={"status": "NOT_READY", "components": components},
    )


def _telemetry_status(request: Request) -> dict[str, object]:
    observability = getattr(request.app.state, "agent_observability", None)
    if observability is None:
        return {
            "state": "UNAVAILABLE",
            "configured": False,
            "provider_created": False,
            "exporter_configured": False,
            "last_export_failure_category": "agent_runtime_unconfigured",
        }
    return observability.status()


def _optional_component(request: Request, state_name: str) -> dict[str, str]:
    configured = getattr(request.app.state, state_name, None) is not None
    return {"state": "CONFIGURED_UNKNOWN" if configured else "DISABLED"}


def _stream_execution_status(request: Request) -> dict[str, object]:
    registry = getattr(request.app.state, "agent_stream_executions", None)
    if registry is None:
        return {"state": "UNAVAILABLE", "active": 0, "capacity": 0}
    return registry.snapshot()


def _database_pool_status(request: Request) -> dict[str, object]:
    observer = getattr(request.app.state, "database_pool_observer", None)
    if observer is None:
        return {"state": "UNAVAILABLE"}
    return observer.snapshot()
