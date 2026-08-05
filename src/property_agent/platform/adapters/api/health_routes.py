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
    svc_up = are_services_configured()

    components = {
        "database": "UP" if db_up else "DOWN",
        "services": "UP" if svc_up else "UNCONFIGURED",
    }

    all_ready = db_up and svc_up

    if all_ready:
        return {"status": "READY", "components": components}

    raise HTTPException(
        status_code=503,
        detail={"status": "NOT_READY", "components": components},
    )