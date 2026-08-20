"""Agent transcript and user-controlled memory contracts."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.adapters.api.dependencies import get_agent_context
from property_agent.agent.adapters.api.memory_router import router
from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.memory_runtime import _load_memories
from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.infrastructure.models import (
    AgentCheckpointModel,
    AgentMemoryModel,
    AgentMessageModel,
    ConversationModel,
)
from property_agent.agent.state import GraphState
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.infrastructure.orm_models import Base

TABLES = [
    ConversationModel.__table__,
    AgentCheckpointModel.__table__,
    AgentMessageModel.__table__,
    AgentMemoryModel.__table__,
]


@dataclass(frozen=True)
class Context:
    actor_id: UUID
    community_id: UUID
    house_ids: frozenset[UUID]
    request_id: str = "req-memory"


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def context() -> Context:
    return Context(uuid4(), uuid4(), frozenset({uuid4()}))


def _start_conversation(session_factory, context: Context, conversation_id: str = "conv-1"):
    ConversationService(session_factory).start(
        conversation_id=conversation_id,
        context=context,
        current_house_id=next(iter(context.house_ids)),
    )


def test_transcript_is_owned_and_updates_conversation_title(session_factory, context):
    _start_conversation(session_factory, context)
    with session_factory() as session:
        service = AgentMemoryService(session)
        service.record_turn(
            conversation_id="conv-1",
            context=context,
            user_text="帮我查看厨房漏水工单",
            assistant_text="已查询到一条处理中工单。",
            house_id=next(iter(context.house_ids)),
            intent="REPAIR",
        )
        messages = service.list_messages("conv-1", context)
        conversations = service.list_conversations(context)

    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert conversations[0]["title"] == "帮我查看厨房漏水工单"
    assert conversations[0]["last_message_at"] is not None


def test_memory_requires_bound_house_and_supports_versioned_delete(session_factory, context):
    with session_factory() as session:
        service = AgentMemoryService(session)
        memory = service.create_memory(
            context,
            memory_type="COMMUNICATION",
            content="上门前请先通过站内消息联系",
            house_id=next(iter(context.house_ids)),
        )
        updated = service.update_memory(
            UUID(memory["id"]),
            context,
            content="上门前请提前半小时通过站内消息联系",
            expected_version=1,
        )
        deleted = service.delete_memory(
            UUID(memory["id"]), context, expected_version=updated["version"]
        )

        assert deleted["deleted"] is True
        assert service.list_memories(context) == []
        with pytest.raises(BusinessError, match="not bound"):
            service.create_memory(
                context,
                memory_type="PREFERENCE",
                content="无效跨房屋记忆",
                house_id=uuid4(),
            )


def test_memory_api_exposes_only_authenticated_users_records(session_factory, context):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_agent_context] = lambda: context

    def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    created = client.post(
        "/api/agent/memories",
        json={"memory_type": "PREFERENCE", "content": "回答尽量简洁"},
    )
    listed = client.get("/api/agent/memories")

    assert created.status_code == 200
    assert created.json()["data"]["confirmed_by_user"] is True
    assert [item["content"] for item in listed.json()["data"]] == ["回答尽量简洁"]


def test_memory_update_cas_rejects_stale_version(session_factory, context):
    with session_factory() as session:
        memory = AgentMemoryService(session).create_memory(
            context, memory_type="PREFERENCE", content="原始偏好"
        )

    # 两个独立会话都基于 version=1 并发更新：先提交者把 version 抬到 2，
    # 后者用过期版本更新时原子 UPDATE 命中 0 行 → VERSION_CONFLICT。
    with session_factory() as s_actor:
        AgentMemoryService(s_actor).update_memory(
            UUID(memory["id"]), context, content="被采纳的更新", expected_version=1
        )
    with session_factory() as s_loser:
        with pytest.raises(BusinessError) as exc_info:
            AgentMemoryService(s_loser).update_memory(
                UUID(memory["id"]), context, content="丢失的更新", expected_version=1
            )
        assert exc_info.value.code == "VERSION_CONFLICT"


def test_memory_delete_cas_rejects_stale_version(session_factory, context):
    with session_factory() as session:
        memory = AgentMemoryService(session).create_memory(
            context, memory_type="PREFERENCE", content="待删除的记忆"
        )

    # 先更新（version 1 -> 2，仍 active），再用过期版本删除 -> CAS 拒绝，
    # 而不是误删或产生 lost update。
    with session_factory() as s_actor:
        AgentMemoryService(s_actor).update_memory(
            UUID(memory["id"]), context, content="中间更新", expected_version=1
        )
    with session_factory() as s_loser:
        with pytest.raises(BusinessError) as exc_info:
            AgentMemoryService(s_loser).delete_memory(
                UUID(memory["id"]), context, expected_version=1
            )
        assert exc_info.value.code == "VERSION_CONFLICT"


def test_expired_memory_is_not_loaded_into_agent_context(session_factory, context):
    with session_factory() as session:
        service = AgentMemoryService(session)
        service.create_memory(
            context,
            memory_type="PREFERENCE",
            content="这条已经过期",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        service.create_memory(
            context,
            memory_type="PREFERENCE",
            content="上门前先联系",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        state = GraphState(
            conversation_id="memory-expiry",
            actor_id=context.actor_id,
            community_id=context.community_id,
            current_house_id=next(iter(context.house_ids)),
        )

        loaded = _load_memories(session, state)

    assert loaded == [{"type": "PREFERENCE", "content": "上门前先联系"}]
