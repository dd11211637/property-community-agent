import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


@pytest.mark.postgres
def test_alembic_upgrade_on_postgres() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    engine = create_engine(database_url)
    if not (engine.url.database or "").endswith("_test"):
        pytest.fail("TEST_POSTGRES_URL must point to a dedicated *_test database")
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert {
        "work_orders",
        "work_order_status_logs",
        "work_order_process_records",
        "work_order_reviews",
    }.issubset(inspector.get_table_names())

    command.downgrade(config, "base")
    assert "work_orders" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "fee_bills" in inspect(engine).get_table_names()
    engine.dispose()
