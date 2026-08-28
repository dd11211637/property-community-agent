"""Real Alibaba Cloud Bailian embedding and PostgreSQL Memory closure."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from property_agent.agent.application.embedding import OpenAICompatibleEmbeddingProvider
from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.infrastructure.models import AgentMemoryModel
from property_agent.agent.memory_contracts import MemoryQuery

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "text-embedding-v4"
VERSION = "bailian-v4-1536-v1"
DIMENSIONS = 1536


@dataclass(frozen=True, slots=True)
class Scope:
    actor_id: UUID
    community_id: UUID
    house_ids: frozenset[UUID]


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _api_key() -> tuple[str, str]:
    for name in ("MEMORY_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value, name
    return "", ""


@contextmanager
def _ephemeral_postgres() -> Iterator[str]:
    name = f"property-agent-memory-{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(24)
    database = "property_agent_real_memory_test"
    _run(["docker", "version", "--format", "{{.Server.Version}}"])
    _run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--env",
            f"POSTGRES_DB={database}",
            "--env",
            "POSTGRES_USER=property_agent",
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "--publish",
            "127.0.0.1::5432",
            "--health-cmd",
            f"pg_isready -U property_agent -d {database}",
            "--health-interval",
            "1s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "30",
            "pgvector/pgvector:pg16",
        ]
    )
    try:
        _wait_for_postgres(name)
        published = _run(["docker", "port", name, "5432/tcp"])
        port = published.rsplit(":", 1)[-1]
        yield f"postgresql+psycopg://property_agent:{password}@127.0.0.1:{port}/{database}"
    finally:
        subprocess.run(["docker", "stop", name], cwd=ROOT, capture_output=True, check=False)


def _wait_for_postgres(name: str) -> None:
    deadline = monotonic() + 45
    while monotonic() < deadline:
        status = _run(["docker", "inspect", "--format", "{{.State.Health.Status}}", name])
        if status == "healthy":
            return
        if status == "unhealthy":
            raise RuntimeError("ephemeral PostgreSQL became unhealthy")
        sleep(1)
    raise RuntimeError("ephemeral PostgreSQL readiness timed out")


def _migrate(database_url: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], env=environment)
    _run([sys.executable, "-m", "alembic", "check"], env=environment)


def _provider(api_key: str) -> OpenAICompatibleEmbeddingProvider:
    base_url = os.getenv("MEMORY_EMBEDDING_BASE_URL", BASE_URL).strip() or BASE_URL
    parsed = urlparse(base_url)
    if not (
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.hostname.endswith("aliyuncs.com")
        and parsed.path.rstrip("/").endswith("compatible-mode/v1")
    ):
        raise RuntimeError("Bailian OpenAI-compatible endpoint is invalid")
    return OpenAICompatibleEmbeddingProvider(
        api_key=api_key,
        base_url=base_url,
        model=MODEL,
        version=VERSION,
        dimensions=DIMENSIONS,
        timeout_seconds=30,
    )


def _query(scope: Scope, house_id: UUID, value: str) -> MemoryQuery:
    return MemoryQuery(
        text=value,
        actor_id=scope.actor_id,
        community_id=scope.community_id,
        current_house_id=house_id,
        bound_house_ids=scope.house_ids,
    )


def _probe_postgres(engine: Engine, vector: tuple[float, ...]) -> dict[str, Any]:
    rendered = "[" + ",".join(str(value) for value in vector) + "]"
    with engine.begin() as connection:
        schema_type = connection.execute(
            text(
                "SELECT format_type(a.atttypid, a.atttypmod) "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = 'agent_memories' AND a.attname = 'embedding'"
            )
        ).scalar_one()
        connection.execute(text("CREATE TEMP TABLE vector_probe (embedding vector(1536))"))
        connection.execute(
            text("INSERT INTO vector_probe (embedding) VALUES (CAST(:value AS vector(1536)))"),
            {"value": rendered},
        )
        stored_dimensions = connection.execute(
            text("SELECT vector_dims(embedding) FROM vector_probe")
        ).scalar_one()
    if schema_type != "vector(1536)" or stored_dimensions != DIMENSIONS:
        raise RuntimeError("pgvector dimension contract mismatch")
    return {"schema_type": schema_type, "stored_dimensions": stored_dimensions}


def _memory_flow(database_url: str, api_key: str, smoke_vector: tuple[float, ...]) -> dict:
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    actor_scope = Scope(uuid4(), uuid4(), frozenset({uuid4()}))
    house_id = next(iter(actor_scope.house_ids))
    content = "维修人员上门前请提前半小时通过站内消息联系"
    postgres = _probe_postgres(engine, smoke_vector)
    with factory() as session:
        service = AgentMemoryService(session, embedding_provider=_provider(api_key))
        created = service.create_memory(
            actor_scope,
            memory_type="COMMUNICATION",
            content=content,
            house_id=house_id,
        )
        memory_id = UUID(created["id"])
        row = session.get(AgentMemoryModel, memory_id)
        own = service.retrieve(_query(actor_scope, house_id, "维修上门如何提前联系"))
        foreign = Scope(uuid4(), actor_scope.community_id, frozenset({house_id}))
        isolated = service.retrieve(_query(foreign, house_id, "维修上门如何提前联系"))
        if row is None or row.embedding_status != "READY" or len(row.embedding or []) != DIMENSIONS:
            raise RuntimeError("Memory embedding persistence contract failed")
        if memory_id not in {item.memory_id for item in own.items} or isolated.items:
            raise RuntimeError("Memory retrieval scope contract failed")
    engine.dispose()
    restarted_engine = create_engine(database_url, pool_pre_ping=True)
    restarted_factory = sessionmaker(bind=restarted_engine, expire_on_commit=False)
    try:
        with restarted_factory() as session:
            restarted = AgentMemoryService(session, embedding_provider=_provider(api_key)).retrieve(
                _query(actor_scope, house_id, "维修上门如何提前联系")
            )
            if memory_id not in {item.memory_id for item in restarted.items}:
                raise RuntimeError("Memory restart retrieval contract failed")
    finally:
        restarted_engine.dispose()
    return {
        "postgres_write": postgres,
        "memory_create": "PASS",
        "embedding_persisted_dimensions": DIMENSIONS,
        "retrieval": "PASS",
        "scope_isolation": "PASS",
        "restart_retrieval": "PASS",
    }


@contextmanager
def _configured_postgres(database_url: str) -> Iterator[str]:
    parsed = urlparse(database_url.replace("postgresql+psycopg", "postgresql"))
    if not parsed.path.removeprefix("/").endswith("_test"):
        raise RuntimeError("TEST_POSTGRES_URL must use a dedicated *_test database")
    yield database_url


def execute() -> dict[str, Any]:
    api_key, credential_source = _api_key()
    if not api_key:
        return {
            "schema_version": "real-memory-embedding-closure-v1",
            "status": "NOT_RUN",
            "external_embedding_gate": "NOT_RUN",
            "reason": "MEMORY_EMBEDDING_API_KEY unavailable",
        }
    try:
        smoke = _provider(api_key).embed("物业服务记忆向量接口连通性验证")
        if len(smoke.vector) != DIMENSIONS:
            raise RuntimeError("provider returned a non-1536 vector")
        configured_database = os.getenv("TEST_POSTGRES_URL", "").strip()
        database_context = (
            _configured_postgres(configured_database)
            if configured_database
            else _ephemeral_postgres()
        )
        with database_context as database_url:
            _migrate(database_url)
            memory = _memory_flow(database_url, api_key, smoke.vector)
        return {
            "schema_version": "real-memory-embedding-closure-v1",
            "status": "PASS",
            "provider": "Alibaba Cloud Bailian OpenAI-compatible",
            "model": MODEL,
            "dimensions": DIMENSIONS,
            "credential_source": credential_source,
            "embedding_api_smoke": "PASS",
            **memory,
            "external_embedding_gate": "PASS",
        }
    except Exception as exc:
        return {
            "schema_version": "real-memory-embedding-closure-v1",
            "status": "FAIL",
            "provider": "Alibaba Cloud Bailian OpenAI-compatible",
            "model": MODEL,
            "dimensions": DIMENSIONS,
            "failure_category": type(exc).__name__,
            "external_embedding_gate": "FAIL",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = execute()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
