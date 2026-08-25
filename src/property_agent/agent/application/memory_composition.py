"""Composition helpers for the governed Memory runtime."""

from dataclasses import dataclass
from typing import Any

from property_agent.agent.application.embedding import OpenAICompatibleEmbeddingProvider
from property_agent.agent.application.memory_runtime import GovernedMemoryReader
from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.application.memory_writer import (
    AcceptedEvidenceMemoryWriter,
    NullMemoryCandidateExtractor,
)


@dataclass(frozen=True, slots=True)
class MemoryRuntime:
    reader: Any
    writer: Any
    embedding_provider: OpenAICompatibleEmbeddingProvider | None


def build_memory_runtime(settings: Any, session_factory: Any, gateway: Any) -> MemoryRuntime:
    provider = _embedding_provider(settings)
    extractor = (
        gateway if getattr(gateway, "extract_candidates", None) else NullMemoryCandidateExtractor()
    )
    writer = AcceptedEvidenceMemoryWriter(
        session_factory,
        extractor,
        service_factory=lambda session: AgentMemoryService(session, embedding_provider=provider),
    )
    return MemoryRuntime(GovernedMemoryReader(session_factory, provider), writer, provider)


def _embedding_provider(settings: Any) -> OpenAICompatibleEmbeddingProvider | None:
    if not settings.memory_embedding_api_key.strip():
        return None
    return OpenAICompatibleEmbeddingProvider(
        api_key=settings.memory_embedding_api_key,
        base_url=settings.memory_embedding_base_url,
        model=settings.memory_embedding_model,
        version=settings.memory_embedding_version,
        dimensions=settings.memory_embedding_dimensions,
        timeout_seconds=settings.memory_embedding_timeout_seconds,
    )
