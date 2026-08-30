import pytest
from jose import JWTError
from pydantic import ValidationError

from property_agent.config import Settings
from property_agent.platform.services import auth


def test_development_profile_allows_local_defaults():
    config = Settings(_env_file=None)
    config.validate_runtime_security()
    assert config.certification_write_enabled is False
    assert config.memory_embedding_base_url == ("https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert config.memory_embedding_model == "text-embedding-v4"
    assert config.memory_embedding_version == "bailian-v4-1536-v1"
    assert config.memory_embedding_dimensions == 1536


def test_memory_embedding_dimension_cannot_diverge_from_pgvector_schema():
    with pytest.raises(ValidationError, match="memory_embedding_dimensions"):
        Settings(memory_embedding_dimensions=1024, _env_file=None)


def test_memory_embedding_model_is_provider_neutral_but_vector_shape_stays_fixed():
    config = Settings(
        memory_embedding_base_url="http://127.0.0.1:11434/v1",
        memory_embedding_model="qwen3-embedding:0.6b",
        memory_embedding_version="ollama-qwen3-0.6b-pad1536-v1",
        memory_embedding_source_dimensions=1024,
        _env_file=None,
    )

    assert config.memory_embedding_model == "qwen3-embedding:0.6b"
    assert config.memory_embedding_source_dimensions == 1024
    assert config.memory_embedding_dimensions == 1536


def test_production_profile_rejects_default_credentials():
    config = Settings(env="production", _env_file=None)

    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        config.validate_runtime_security()


def test_production_profile_accepts_explicit_secure_configuration():
    config = Settings(
        env="production",
        jwt_secret="production-secret-that-is-longer-than-thirty-two-characters",
        database_url="postgresql+psycopg://property_app:strong-password@database/property_agent",
        otel_exporter_endpoint="http://otel-collector:4318",
        _env_file=None,
    )

    config.validate_runtime_security()


def test_local_v2_profile_is_limited_to_local_candidate_deployments():
    config = Settings(
        env="development",
        deployment_environment="production",
        agent_runtime_profile="local-v2",
        _env_file=None,
    )
    with pytest.raises(RuntimeError, match="local-use or release-candidate"):
        config.validate_runtime_security()


def test_local_v2_profile_accepts_local_use():
    config = Settings(
        env="development",
        deployment_environment="local-use",
        agent_runtime_profile="local-v2",
        _env_file=None,
    )
    config.validate_runtime_security()


def test_production_profile_rejects_enabled_telemetry_without_exporter():
    config = Settings(
        env="production",
        jwt_secret="production-secret-that-is-longer-than-thirty-two-characters",
        database_url="postgresql+psycopg://property_app:strong-password@database/property_agent",
        otel_exporter_endpoint="",
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="OTEL_EXPORTER_ENDPOINT"):
        config.validate_runtime_security()


def test_production_profile_rejects_disabled_concurrency_guard():
    config = Settings(
        env="production",
        jwt_secret="production-secret-that-is-longer-than-thirty-two-characters",
        database_url="postgresql+psycopg://property_app:strong-password@database/property_agent",
        agent_concurrency_guard=False,
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="AGENT_CONCURRENCY_GUARD"):
        config.validate_runtime_security()


def test_production_profile_rejects_nonpositive_lease_and_ttl():
    config = Settings(
        env="production",
        jwt_secret="production-secret-that-is-longer-than-thirty-two-characters",
        database_url="postgresql+psycopg://property_app:strong-password@database/property_agent",
        agent_run_lease_seconds=0,
        agent_approval_ttl_minutes=0,
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="AGENT_RUN_LEASE_SECONDS|AGENT_APPROVAL_TTL_MINUTES"):
        config.validate_runtime_security()


def test_production_profile_rejects_unbounded_stream_execution_settings():
    config = Settings(
        env="production",
        jwt_secret="production-secret-that-is-longer-than-thirty-two-characters",
        database_url="postgresql+psycopg://property_app:strong-password@database/property_agent",
        otel_exporter_endpoint="http://otel-collector:4318",
        agent_stream_max_concurrency=0,
        agent_stream_shutdown_grace_seconds=0,
        _env_file=None,
    )

    with pytest.raises(
        RuntimeError,
        match="AGENT_STREAM_MAX_CONCURRENCY|AGENT_STREAM_SHUTDOWN_GRACE_SECONDS",
    ):
        config.validate_runtime_security()


def test_jwt_signing_uses_unified_settings(monkeypatch):
    from uuid import uuid4

    monkeypatch.setattr(auth.settings, "jwt_secret", "a" * 40)
    token = auth.create_jwt_token(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=["RESIDENT"],
        bound_house_ids=[],
    )
    assert auth.decode_jwt_token(token)["roles"] == ["RESIDENT"]

    monkeypatch.setattr(auth.settings, "jwt_secret", "b" * 40)
    with pytest.raises(JWTError):
        auth.decode_jwt_token(token)
