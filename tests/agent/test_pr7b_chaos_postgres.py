"""Real PostgreSQL dependency interruption and process-death certification evidence."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from property_agent.agent.application.langgraph_runtime import build_saver_resource
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.state import GraphState
from property_agent.platform.container import check_database_health
from property_agent.platform.infrastructure.orm_models import Base
from property_agent.repair.infrastructure.models import WorkOrderModel
from testing.pr7b.crash_worker import (
    CRASH_EXIT_CODE,
    commit_repair,
    write_internal_checkpoint,
)

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="requires TEST_POSTGRES_URL"),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def provision_application_tables():
    """Restore metadata after the earlier PR6 pgvector fixture drops all app tables."""
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    engine.dispose()


def test_c4_transient_postgres_interruption_rejects_failed_transaction_and_recovers():
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    marker = f"pr7b-db-interrupt-{uuid4()}"
    connection = engine.connect()
    target_url = make_url(str(POSTGRES_URL))
    database_name = str(target_url.database)
    assert database_name.endswith("_test")
    admin = create_engine(target_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as controller:
            controller.execute(text(f'ALTER DATABASE "{database_name}" ALLOW_CONNECTIONS false'))
            controller.execute(
                text("select pg_terminate_backend(pid) from pg_stat_activity where datname=:name"),
                {"name": database_name},
            )
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "insert into agent_checkpoints "
                    "(thread_id, version, state, pending_confirm) "
                    "values (:marker, 1, '{}'::jsonb, false)"
                ),
                {"marker": marker},
            )
        assert _run_health_check() is False
    finally:
        connection.close()
        with admin.connect() as controller:
            controller.execute(text(f'ALTER DATABASE "{database_name}" ALLOW_CONNECTIONS true'))
        admin.dispose()
    assert _run_health_check() is True
    with engine.begin() as recovered:
        count = recovered.execute(
            text("select count(*) from agent_checkpoints where thread_id=:marker"),
            {"marker": marker},
        ).scalar_one()
        assert count == 0
    engine.dispose()


def test_c7_process_death_after_internal_checkpoint_recovers_exact_accepted_cursor():
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    conversation_id = f"pr7b-c7-{uuid4()}"
    internal_thread = f"lg:{conversation_id}:crash-window"
    accepted_cursor = write_internal_checkpoint(POSTGRES_URL, internal_thread)
    state = GraphState(conversation_id=conversation_id, slots={"user_text": "safe fixture"})
    checkpointer = SqlAlchemyCheckpointer(sessions)
    checkpointer.publish_accepted(
        conversation_id, state, expected_version=0, runtime_cursor=accepted_cursor
    )

    completed = _crash_process("checkpoint", "--thread-id", internal_thread)
    assert completed.returncode == CRASH_EXIT_CODE

    recovered = SqlAlchemyCheckpointer(sessions).load_accepted(conversation_id)
    assert recovered is not None and recovered.runtime_cursor is not None
    assert recovered.runtime_cursor.to_dict() == accepted_cursor
    resource = build_saver_resource(dsn=POSTGRES_URL.replace("postgresql+psycopg", "postgresql"))
    try:
        exact = resource.saver.get_tuple({"configurable": accepted_cursor})
        latest = resource.saver.get_tuple(
            {"configurable": {"thread_id": internal_thread, "checkpoint_ns": ""}}
        )
        assert exact is not None and latest is not None
        assert latest.config["configurable"]["checkpoint_id"] != accepted_cursor["checkpoint_id"]
    finally:
        resource.close()
        engine.dispose()


def test_c8_business_commit_then_process_death_retries_same_resource_once():
    actor, community, house = uuid4(), uuid4(), uuid4()
    key = f"pr7b-c8-{uuid4()}"
    completed = _crash_process(
        "business",
        "--actor-id",
        str(actor),
        "--community-id",
        str(community),
        "--house-id",
        str(house),
        "--idempotency-key",
        key,
    )
    assert completed.returncode == CRASH_EXIT_CODE

    replay = commit_repair(
        POSTGRES_URL,
        actor_id=actor,
        community_id=community,
        house_id=house,
        idempotency_key=key,
    )
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    select(WorkOrderModel).where(WorkOrderModel.create_idempotency_key == key)
                )
                .scalars()
                .all()
            )
            count = connection.execute(
                select(func.count())
                .select_from(WorkOrderModel)
                .where(WorkOrderModel.create_idempotency_key == key)
            ).scalar_one()
        assert count == 1
        assert str(rows[0]) == str(replay.id)
    finally:
        engine.dispose()


def _crash_process(mode: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "testing.pr7b.crash_worker",
            mode,
            "--database-url",
            str(POSTGRES_URL),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def _run_health_check() -> bool:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(check_database_health())
