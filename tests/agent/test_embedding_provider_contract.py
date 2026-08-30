from __future__ import annotations

import json

import httpx

from property_agent.agent.application.embedding import OpenAICompatibleEmbeddingProvider
from property_agent.agent.infrastructure.models import AgentMemoryModel


def test_openai_compatible_provider_sends_bailian_dimensions_contract() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["Authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.0] * 1536}]},
        )

    provider = OpenAICompatibleEmbeddingProvider(
        api_key="contract-secret",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="text-embedding-v4",
        version="bailian-v4-1536-v1",
        dimensions=1536,
        transport=httpx.MockTransport(handler),
    )

    result = provider.embed("维修上门前请通过站内消息联系")

    assert observed == {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        "authorization": "Bearer contract-secret",
        "payload": {
            "model": "text-embedding-v4",
            "input": "维修上门前请通过站内消息联系",
            "dimensions": 1536,
        },
    }
    assert len(result.vector) == 1536
    assert result.model == "text-embedding-v4"


def test_memory_pgvector_schema_remains_fixed_at_1536() -> None:
    vector_type = AgentMemoryModel.__table__.c.embedding.type
    assert vector_type.dim == 1536


def test_local_embedding_can_zero_pad_without_changing_source_values() -> None:
    source = [0.25] * 1024
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="local-ollama",
        base_url="http://host.docker.internal:11434/v1",
        model="qwen3-embedding:0.6b",
        version="ollama-qwen3-0.6b-pad1536-v1",
        dimensions=1536,
        source_dimensions=1024,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"data": [{"embedding": source}]})
        ),
    )

    result = provider.embed("本地真实记忆")

    assert list(result.vector[:1024]) == source
    assert result.vector[1024:] == (0.0,) * 512
