"""
Application configuration — PRD 5.4 Composition Root.

Uses pydantic-settings to load configuration from environment variables and .env files.
All production settings have sensible defaults for development; override via .env or
environment variables in production.
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEVELOPMENT_JWT_SECRET = "dev-secret-change-in-production-32chars-min"


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
    jwt_secret: str = _DEVELOPMENT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 8
    bcrypt_rounds: int = 12
    login_failure_limit: int = 5
    login_failure_window_minutes: int = 15
    login_lock_minutes: int = 15
    trusted_proxy_cidrs: str = "127.0.0.0/8,::1/128"

    # ── Server ───────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    slow_request_threshold_ms: int = 1000

    # ── DeepSeek model gateway ───────────────────────────────────
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_api_key: str = ""
    deepseek_connect_timeout_seconds: float = 3.0
    deepseek_read_timeout_seconds: float = 12.0
    deepseek_total_timeout_seconds: float = 6.0

    # ── Provider-neutral long-term-memory embeddings ────────────
    memory_embedding_base_url: str = "https://api.openai.com/v1"
    memory_embedding_api_key: str = ""
    memory_embedding_model: str = "text-embedding-3-small"
    memory_embedding_version: str = "1"
    memory_embedding_dimensions: int = 1536
    memory_embedding_timeout_seconds: float = 6.0

    # ── Agent concurrency guards (P0 正确性底座) ──────────────────
    # 关闭后回退到「单凭 confirmation token」旧行为，便于回滚/排错；
    # 默认开启。生产环境必须保持开启，避免同会话并发 lost-update。
    agent_concurrency_guard: bool = True
    # 审批有效窗口：与 ConfirmationService 的 5 分钟 TTL 对齐（PF-04）。
    agent_approval_ttl_minutes: int = 5
    # 单 turn lease 时长：足够覆盖一次 LLM 调用，又短到过期后能快速抢占。
    agent_run_lease_seconds: int = 30
    agent_stream_max_concurrency: int = 16
    agent_stream_shutdown_grace_seconds: float = 15.0

    # ── OpenTelemetry 可观测性（PR7-A） ──────────────────────────
    otel_enabled: bool = True
    otel_service_name: str = "property-agent"
    otel_exporter_endpoint: str = ""
    otel_export_interval_ms: int = 30_000
    release_sha: str = ""
    deployment_environment: str = ""
    certification_write_enabled: bool = False

    def validate_runtime_security(self) -> None:
        """Reject development credentials when the production profile is selected."""
        if self.env.strip().lower() != "production":
            return

        problems: list[str] = []
        if self.jwt_secret == _DEVELOPMENT_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be a non-default secret of at least 32 characters")

        parsed_database = urlparse(self.database_url.replace("postgresql+psycopg", "postgresql"))
        if parsed_database.username == "postgres" and parsed_database.password == "postgres":
            problems.append("DATABASE_URL must not use the default postgres credentials")
        if not parsed_database.hostname:
            problems.append("DATABASE_URL must include an explicit database host")

        if self.deepseek_connect_timeout_seconds <= 0:
            problems.append("DEEPSEEK_CONNECT_TIMEOUT_SECONDS must be positive")
        if self.deepseek_read_timeout_seconds <= 0:
            problems.append("DEEPSEEK_READ_TIMEOUT_SECONDS must be positive")
        if self.deepseek_total_timeout_seconds <= 0:
            problems.append("DEEPSEEK_TOTAL_TIMEOUT_SECONDS must be positive")
        if self.memory_embedding_dimensions != 1536:
            problems.append("MEMORY_EMBEDDING_DIMENSIONS must match the pgvector schema (1536)")
        if self.memory_embedding_timeout_seconds <= 0:
            problems.append("MEMORY_EMBEDDING_TIMEOUT_SECONDS must be positive")
        if self.otel_enabled and not self.otel_exporter_endpoint.strip():
            problems.append("OTEL_EXPORTER_ENDPOINT is required when OTEL_ENABLED is true")
        if self.otel_export_interval_ms <= 0:
            problems.append("OTEL_EXPORT_INTERVAL_MS must be positive")
        if self.certification_write_enabled and self.deployment_environment not in {
            "preproduction",
            "isolated-test",
        }:
            problems.append(
                "CERTIFICATION_WRITE_ENABLED requires preproduction or isolated-test deployment"
            )

        if self.login_failure_limit <= 0:
            problems.append("LOGIN_FAILURE_LIMIT must be positive")
        if self.login_failure_window_minutes <= 0:
            problems.append("LOGIN_FAILURE_WINDOW_MINUTES must be positive")
        if self.login_lock_minutes <= 0:
            problems.append("LOGIN_LOCK_MINUTES must be positive")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            problems.append("LOG_LEVEL must be a supported Python logging level")
        if self.slow_request_threshold_ms <= 0:
            problems.append("SLOW_REQUEST_THRESHOLD_MS must be positive")

        # ── Agent concurrency guards（P0 正确性底座，禁止在生产关闭）──
        # 关闭 guard 会回退到「单凭 confirmation token」旧行为，导致同会话
        # 并发 lost-update；lease / approval 窗口非正也会让抢占与审批失效。
        if not self.agent_concurrency_guard:
            problems.append("AGENT_CONCURRENCY_GUARD must stay enabled in production")
        if self.agent_run_lease_seconds <= 0:
            problems.append("AGENT_RUN_LEASE_SECONDS must be positive")
        if self.agent_approval_ttl_minutes <= 0:
            problems.append("AGENT_APPROVAL_TTL_MINUTES must be positive")
        if self.agent_stream_max_concurrency <= 0:
            problems.append("AGENT_STREAM_MAX_CONCURRENCY must be positive")
        if self.agent_stream_shutdown_grace_seconds <= 0:
            problems.append("AGENT_STREAM_SHUTDOWN_GRACE_SECONDS must be positive")

        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


# Singleton instance — import this throughout the application
settings = Settings()
