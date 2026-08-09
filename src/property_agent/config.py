"""
Application configuration — PRD 5.4 Composition Root.

Uses pydantic-settings to load configuration from environment variables and .env files.
All production settings have sensible defaults for development; override via .env or
environment variables in production.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from .env and environment variables.

    Usage::

        from property_agent.config import settings
        print(settings.database_url)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Environment ──────────────────────────────────────────────
    env: str = "development"
    debug: bool = False

    # ── Database ─────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost/property_agent"

    # ── JWT / Auth ───────────────────────────────────────────────
    jwt_secret: str = "dev-secret-change-in-production-32chars-min"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 8

    # ── Server ───────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000


# Singleton instance — import this throughout the application
settings = Settings()
