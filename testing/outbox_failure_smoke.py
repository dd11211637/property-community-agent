"""Exercise retry exhaustion and manual handover against the real demo database."""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from property_agent.platform.infrastructure.orm_models import (
    HandoverTicketModel,
    MessageRecordModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import OutboxDispatcher

MANAGER_A = UUID("a2000000-0000-0000-0000-000000000060")


async def run(database_url: str) -> dict[str, object]:
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    message_id = uuid4()
    with factory() as session:
        session.add(
            MessageRecordModel(
                id=message_id,
                receiver_id=MANAGER_A,
                business_type="BILLING",
                resource_id=f"e2e-failure-{uuid4().hex}",
                title="E2E 消息投递失败",
                body="用于验证重试上限与人工接管。",
                status="PENDING",
                idempotency_key=f"e2e-outbox-failure-{uuid4().hex}",
            )
        )
        session.commit()

    async def fail_transport(message: MessageRecordModel) -> bool:
        return False

    dispatcher = OutboxDispatcher(
        session_factory=factory,
        send_message=fail_transport,
        max_retry=1,
    )
    processed = await dispatcher.run_once()
    with factory() as session:
        message = session.get(MessageRecordModel, message_id)
        handover = (
            session.query(HandoverTicketModel)
            .filter_by(resource_type="MESSAGE", resource_id=str(message_id))
            .one()
        )
        result = {
            "processed": processed,
            "message_id": str(message_id),
            "message_status": message.status,
            "retry_count": message.retry_count,
            "handover_id": str(handover.id),
            "handover_status": handover.status,
            "handover_queue": handover.queue,
        }
    engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default="postgresql+psycopg://property_agent:demo-password@localhost:5432/property_agent_demo",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.database_url)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
