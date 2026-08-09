"""Guarded Alembic reset for the local Docker demo database only."""

from __future__ import annotations

import argparse
import os

from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command
from testing.seeds.seed_demo import seed

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}
ALLOWED_DATABASE = "property_agent_demo"


def validate_target(database_url: str, environment: str, confirmed: bool) -> None:
    """Reject every target except the explicitly confirmed local demo DB."""
    if environment.lower() != "demo":
        raise RuntimeError("Reset refused: ENV must be 'demo'.")
    if not confirmed:
        raise RuntimeError("Reset refused: pass --yes to confirm destructive reset.")
    url = make_url(database_url)
    if url.host not in ALLOWED_HOSTS or url.database != ALLOWED_DATABASE:
        raise RuntimeError(
            "Reset refused: target must be property_agent_demo on localhost or Compose postgres."
        )


def reset(database_url: str, environment: str, confirmed: bool) -> None:
    validate_target(database_url, environment, confirmed)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    seed()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="confirm destructive demo reset")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("Reset refused: DATABASE_URL is required.")
    reset(database_url, os.environ.get("ENV", ""), args.yes)


if __name__ == "__main__":
    main()
