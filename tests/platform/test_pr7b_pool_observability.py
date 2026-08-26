from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from property_agent.agent.observability import AgentObservability
from property_agent.platform.infrastructure.pool_observability import install_pool_observability


def test_existing_queue_pool_emits_checkout_checkin_usage_and_capacity_without_reconfiguration():
    engine = create_engine("sqlite://", poolclass=QueuePool, pool_size=2, max_overflow=1)
    observation = AgentObservability.in_memory()
    observer = install_pool_observability(engine, observation)
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar_one() == 1
    observer.timeout()

    names = [point.name for point in observation.points]
    assert "database_pool_checkout_total" in names
    assert "database_pool_checkin_total" in names
    assert "database_pool_connections_in_use" in names
    assert "database_pool_base_capacity" in names
    assert "database_pool_current_overflow" in names
    assert "database_pool_overflow_allowance" in names
    assert "database_pool_connection_use_duration_seconds" in names
    timeout = [
        point
        for point in observation.points
        if point.name == "database_pool_checkout_total"
        and point.attributes.get("reason") == "timeout"
    ]
    assert len(timeout) == 1
    snapshot = observer.snapshot()
    assert snapshot["checkout_total"] == 1
    assert snapshot["checkin_total"] == 1
    assert snapshot["timeout_total"] == 1
    assert snapshot["base_capacity"] == 2
    assert snapshot["overflow_allowance"] == 1
    assert engine.pool.size() == 2
    assert engine.pool._max_overflow == 1
    engine.dispose()


def test_reinstall_retargets_observation_without_duplicate_pool_listeners():
    engine = create_engine("sqlite://", poolclass=QueuePool)
    first = AgentObservability.in_memory()
    second = AgentObservability.in_memory()
    original = install_pool_observability(engine, first)
    replacement = install_pool_observability(engine, second)
    assert replacement is original
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    assert not any(point.name == "database_pool_checkout_total" for point in first.points)
    assert sum(point.name == "database_pool_checkout_total" for point in second.points) == 1
    engine.dispose()
