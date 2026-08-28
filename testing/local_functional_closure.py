"""Self-contained local functional closure for PR7 support validation.

This support entry point creates an ephemeral SQLite database, JWT secret, demo
identities, houses, release identity, and in-process ASGI target. It never needs
operator-provided auth, database, account, house, release, or localhost settings.
External model credentials remain separate truthful gates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def _release_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if len(value) != 40:
        raise RuntimeError("exact release SHA could not be derived")
    return value


def _configure_ephemeral_environment(database_path: Path, release_sha: str) -> None:
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    os.environ.update(
        {
            "DATABASE_URL": database_url,
            "JWT_SECRET": secrets.token_urlsafe(48),
            "DEPLOYMENT_ENVIRONMENT": "isolated-test",
            "RELEASE_SHA": release_sha,
            "CERTIFICATION_WRITE_ENABLED": "false",
        }
    )


def _build_local_app() -> Any:
    from property_agent.main import create_app
    from property_agent.platform.container import build_production_container
    from property_agent.platform.infrastructure.database import init_db
    from testing.seeds.seed_demo import seed

    init_db()
    seed()
    app = create_app()
    build_production_container(app)
    return app


def _login_for_load(client: TestClient) -> tuple[str, str]:
    response = client.post("/api/auth/login", json={"username": "zhangsan", "password": "123456"})
    response.raise_for_status()
    payload = response.json()
    house_id = str(payload["current_house_id"])
    if not house_id:
        raise RuntimeError("generated resident has no bound house")
    return str(payload["access_token"]), house_id


async def _run_load_smoke(app: Any, token: str, house_id: str):
    from testing.pr7b.capacity import CapacityBounds
    from testing.pr7b.load_gate import LoadProfile, execute

    profile = LoadProfile(
        base_url="http://local.generated",
        token=token,
        house_id=house_id,
        environment="isolated-test",
        expected_concurrency=1,
        sustained_seconds=1,
        spike_seconds=1,
        allow_writes=False,
        smoke=True,
    )
    bounds = CapacityBounds(
        expected_concurrency=1,
        max_concurrency=2,
        max_conversations=8,
        max_requests=500,
        max_write_operations=0,
        max_run_seconds=2,
    )
    return await execute(profile, bounds, transport=httpx.ASGITransport(app=app))


def run_local_closure() -> dict[str, Any]:
    release_sha = _release_sha()
    with TemporaryDirectory(prefix="property-agent-local-closure-") as directory:
        _configure_ephemeral_environment(Path(directory) / "functional.db", release_sha)
        app = _build_local_app()
        client = TestClient(app)
        try:
            from testing.e2e_api_smoke import run as run_api_smoke

            api_result = run_api_smoke("http://local.generated", client=client)
            token, house_id = _login_for_load(client)
            load_evidence = asyncio.run(_run_load_smoke(app, token, house_id))
        finally:
            client.close()
            from property_agent.platform.infrastructure.database import dispose_engine

            dispose_engine()

    load_status = load_evidence.status.value
    return {
        "schema_version": "pr7-local-functional-closure-v1",
        "status": "PASS" if load_status == "PASS" else "FAIL",
        "release_sha": release_sha,
        "generated_inputs": {
            "database": True,
            "jwt": True,
            "demo_accounts": True,
            "house_binding": True,
            "release_sha": True,
            "in_process_url": True,
        },
        "api_smoke": api_result,
        "load_harness_smoke": {
            "status": load_status,
            "requests": load_evidence.sample_counts.get("requests", 0),
            "failures": load_evidence.sample_counts.get("failures", 0),
            "limitations": list(load_evidence.limitations),
        },
        "external_gates": {
            "real_model": (
                "AVAILABLE_FOR_SEPARATE_GATE"
                if os.getenv("DEEPSEEK_API_KEY", "").strip()
                else "NOT_RUN: DEEPSEEK_API_KEY unavailable"
            ),
            "embedding_provider": (
                "AVAILABLE_FOR_SEPARATE_GATE"
                if os.getenv("MEMORY_EMBEDDING_API_KEY", "").strip()
                else "NOT_RUN: MEMORY_EMBEDDING_API_KEY unavailable"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_local_closure()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
