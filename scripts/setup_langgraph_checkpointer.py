"""Explicitly install the official LangGraph PostgreSQL checkpoint schema."""

from property_agent.agent.application.langgraph_runtime import build_saver_resource
from property_agent.config import settings


def main() -> None:
    dsn = settings.database_url.replace("postgresql+psycopg", "postgresql")
    if dsn.lower().startswith("sqlite"):
        raise SystemExit("LangGraph PostgreSQL setup requires a PostgreSQL DATABASE_URL")
    resource = build_saver_resource(dsn=dsn)
    try:
        resource.saver.setup()
    finally:
        resource.close()


if __name__ == "__main__":
    main()
