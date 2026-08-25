"""Required Agent store readiness checks kept separate from liveness."""

from sqlalchemy import text

from property_agent.platform.container import get_async_engine


async def check_accepted_head_store() -> bool:
    """Verify the required accepted-head schema is reachable, including when empty."""
    try:
        async with get_async_engine().connect() as connection:
            await connection.execute(text("SELECT version FROM agent_checkpoints LIMIT 1"))
        return True
    except Exception:
        return False


__all__ = ["check_accepted_head_store"]
