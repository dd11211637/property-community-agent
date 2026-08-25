"""Production adapters that connect persisted memories to the Agent runtime."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from property_agent.agent.application.memory_service import (
    AgentMemoryService,
)
from property_agent.agent.application.memory_service import (
    MemoryContext as ActorMemoryContext,
)
from property_agent.agent.memory_contracts import (
    EmbeddingProvider,
    MemoryContext,
    MemoryQuery,
)
from property_agent.agent.runtime import RuntimeContext
from property_agent.agent.state import GraphState
from property_agent.platform.infrastructure.orm_models import CommunityModel, HouseModel

SessionFactory = Callable[[], Session]


def _display_part(value: Any, suffix: str) -> str:
    text = str(value or "").strip()
    return text if text.endswith(suffix) else f"{text}{suffix}"


def build_agent_context_loader(session_factory: SessionFactory):
    """Build the trusted display context loader used before model analysis."""

    def load(state: GraphState) -> GraphState:
        trusted: dict[str, Any] = {
            "business_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        }
        with session_factory() as session:
            _load_place_context(session, state, trusted)
            memories = _load_memories(session, state)
            if memories:
                trusted["user_confirmed_memories"] = memories
                trusted["memory_context_authority"] = "UNTRUSTED_REVISABLE_MEMORY"
        state.trusted_context = trusted
        return state

    return load


def _load_place_context(session: Session, state: GraphState, trusted: dict[str, Any]) -> None:
    community = session.get(CommunityModel, state.community_id)
    if community is not None:
        trusted["community_name"] = community.name
    if state.current_house_id is None:
        return
    house = session.get(HouseModel, state.current_house_id)
    if house is None or house.community_id != state.community_id:
        return
    building = _display_part(house.building, "栋")
    unit = _display_part(house.unit, "单元")
    room = _display_part(house.room_no, "室")
    trusted.update(
        {
            "building": house.building,
            "unit": house.unit,
            "room_no": house.room_no,
            "house_display": f"{building} {unit} {room}",
        }
    )


def _load_memories(session: Session, state: GraphState) -> list[dict[str, str]]:
    if state.actor_id is None or state.community_id is None:
        return []
    bound = frozenset({state.current_house_id}) if state.current_house_id else frozenset()
    result = AgentMemoryService(session).retrieve(
        MemoryQuery(
            text=str(state.slots.get("user_text") or ""),
            actor_id=state.actor_id,
            community_id=state.community_id,
            current_house_id=state.current_house_id,
            bound_house_ids=bound,
            limit=10,
        )
    )
    return [{"type": item.memory_type, "content": item.content} for item in result.items]


class GovernedMemoryReader:
    """Open one scoped repository transaction for each v2 planning retrieval."""

    def __init__(
        self, session_factory: SessionFactory, embedding_provider: EmbeddingProvider | None = None
    ) -> None:
        self._sessions = session_factory
        self._embedding = embedding_provider

    def __call__(self, text: str, runtime: RuntimeContext):
        with self._sessions() as session:
            return AgentMemoryService(session, embedding_provider=self._embedding).retrieve(
                MemoryQuery(
                    text=text,
                    actor_id=runtime.actor_id,
                    community_id=runtime.community_id,
                    current_house_id=runtime.current_house_id,
                    bound_house_ids=runtime.bound_house_ids,
                )
            )

    def revalidate(
        self, text: str, runtime: RuntimeContext, previous: MemoryContext
    ) -> MemoryContext:
        query = MemoryQuery(
            text=text,
            actor_id=runtime.actor_id,
            community_id=runtime.community_id,
            current_house_id=runtime.current_house_id,
            bound_house_ids=runtime.bound_house_ids,
            limit=min(20, len(previous.items)),
        )
        with self._sessions() as session:
            return AgentMemoryService(session, embedding_provider=self._embedding).revalidate(
                query, {item.memory_id for item in previous.items}
            )


def build_turn_recorder(session_factory: SessionFactory):
    """Build an append-only transcript recorder with request identity supplied by the runner."""

    def record(
        context: ActorMemoryContext,
        state: GraphState,
        user_text: str,
        assistant_text: str,
    ) -> None:
        with session_factory() as session:
            AgentMemoryService(session).record_turn(
                conversation_id=state.conversation_id,
                context=context,
                user_text=user_text,
                assistant_text=assistant_text,
                house_id=state.current_house_id,
                intent=state.intent,
            )

    return record
