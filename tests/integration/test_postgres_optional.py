import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


@pytest.mark.postgres
def test_alembic_upgrade_on_postgres() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert {
        "bills",
        "inspection_tasks",
        "security_events",
        "work_orders",
        "work_order_status_logs",
        "work_order_process_records",
        "work_order_reviews",
    }.issubset(inspector.get_table_names())
