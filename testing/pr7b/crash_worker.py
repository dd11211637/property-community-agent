"""Subprocess workers for real PR7-B crash-window tests."""

from __future__ import annotations

import argparse
import os
from typing import Any, TypedDict
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from property_agent.agent.application.langgraph_runtime import build_saver_resource
from property_agent.repair.application.commands import CreateWorkOrderCommand
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.enums import RepairCategory, Role, Urgency
from property_agent.repair.infrastructure.shared_ports import SqlAlchemyIdempotencyPort
from property_agent.repair.infrastructure.uow import SharedPorts, SqlAlchemyRepairUnitOfWork

CRASH_EXIT_CODE = 86


class _CheckpointState(TypedDict):
    value: int


def write_internal_checkpoint(database_url: str, thread_id: str) -> dict[str, str | None]:
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(_CheckpointState)
    graph.add_node("advance", lambda state: {"value": int(state.get("value", 0)) + 1})
    graph.add_edge(START, "advance")
    graph.add_edge("advance", END)
    resource = build_saver_resource(dsn=database_url.replace("postgresql+psycopg", "postgresql"))
    try:
        resource.saver.setup()
        compiled = graph.compile(checkpointer=resource.saver)
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        compiled.invoke({"value": 0}, config)
        latest = resource.saver.get_tuple(config)
        if latest is None:
            raise RuntimeError("official LangGraph saver did not persist a checkpoint")
        configurable = latest.config["configurable"]
        return {
            "thread_id": str(configurable["thread_id"]),
            "checkpoint_ns": str(configurable.get("checkpoint_ns") or ""),
            "checkpoint_id": str(configurable["checkpoint_id"]),
        }
    finally:
        resource.close()


def build_repair_service(database_url: str) -> WorkOrderService:
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def ports(session: Session) -> SharedPorts:
        noop = _NoopPort()
        return SharedPorts(
            idempotency=SqlAlchemyIdempotencyPort(session),
            confirmations=noop,
            house_access=noop,
            staff_directory=noop,
            attachments=noop,
            audit=noop,
            messages=noop,
            handover=noop,
        )

    return WorkOrderService(lambda: SqlAlchemyRepairUnitOfWork(sessions, ports))


def commit_repair(
    database_url: str,
    *,
    actor_id: UUID,
    community_id: UUID,
    house_id: UUID,
    idempotency_key: str,
):
    service = build_repair_service(database_url)
    context = RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=frozenset({Role.RESIDENT}),
        request_id="pr7b-crash-window",
        house_ids=frozenset({house_id}),
    )
    command = CreateWorkOrderCommand(
        house_id=house_id,
        category=RepairCategory.WATER_PLUMBING,
        location="测试厨房",
        description="隔离测试环境的水管故障",
        urgency=Urgency.NORMAL,
        confirmation_token="confirmed",
        approval_ref="pr7b-synthetic-approval",
    )
    return service.create(command, context, idempotency_key=idempotency_key)


class _NoopPort:
    def consume(self, **_kwargs: Any) -> None:
        return None

    def ensure_access(self, **_kwargs: Any) -> None:
        return None

    def ensure_usable(self, **_kwargs: Any) -> None:
        return None

    def ensure_repair_worker(self, **_kwargs: Any) -> None:
        return None

    def list_duty_staff(self, **_kwargs: Any) -> tuple[()]:
        return ()

    def add(self, **_kwargs: Any) -> None:
        return None

    def enqueue(self, **_kwargs: Any) -> None:
        return None

    def create(self, **_kwargs: Any):
        return uuid4()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("checkpoint", "business"))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--thread-id")
    parser.add_argument("--actor-id")
    parser.add_argument("--community-id")
    parser.add_argument("--house-id")
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    if args.mode == "checkpoint":
        write_internal_checkpoint(args.database_url, str(args.thread_id))
    else:
        commit_repair(
            args.database_url,
            actor_id=UUID(str(args.actor_id)),
            community_id=UUID(str(args.community_id)),
            house_id=UUID(str(args.house_id)),
            idempotency_key=str(args.idempotency_key),
        )
    os._exit(CRASH_EXIT_CODE)


if __name__ == "__main__":
    raise SystemExit(main())
