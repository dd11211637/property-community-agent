"""
Alembic environment configuration — uses the unified Platform Base.

All domain modules (repair, inspection, billing, platform) share
the same Base metadata from platform.infrastructure.orm_models.
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from property_agent.agent.infrastructure import models as agent_models  # noqa: F401
from property_agent.announcement.infrastructure import models as announcement_models  # noqa: F401
from property_agent.billing.infrastructure import orm_models as billing_models  # noqa: F401
from property_agent.inspection.infrastructure import models as inspection_models  # noqa: F401
from property_agent.platform.infrastructure.orm_models import Base
from property_agent.repair.infrastructure import models as repair_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
