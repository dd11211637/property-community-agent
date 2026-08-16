"""Production adapters that connect persisted memories to the Agent runtime."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from property_agent.agent.application.memory_service import AgentMemoryService, MemoryContext
from property_agent.agent.infrastructure.models import AgentMemoryModel
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
    now = datetime.now(UTC)
    rows = (
        session.query(AgentMemoryModel)
        .filter(
            AgentMemoryModel.actor_id == state.actor_id,
            AgentMemoryModel.community_id == state.community_id,
            AgentMemoryModel.deleted_at.is_(None),
            or_(AgentMemoryModel.expires_at.is_(None), AgentMemoryModel.expires_at > now),
            (
                AgentMemoryModel.house_id.is_(None)
                | (AgentMemoryModel.house_id == state.current_house_id)
            ),
        )
        .order_by(AgentMemoryModel.updated_at.desc())
        .limit(10)
        .all()
    )
    return [{"type": item.memory_type, "content": item.content} for item in rows]


def build_turn_recorder(session_factory: SessionFactory):
    """Build an append-only transcript recorder with request identity supplied by the runner."""

    def record(
        context: MemoryContext,
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
