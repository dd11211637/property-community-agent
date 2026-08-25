"""Provider-neutral OpenAI-compatible embedding adapter."""

from __future__ import annotations

from typing import Any

import httpx

from property_agent.agent.memory_contracts import EmbeddingResult


class EmbeddingUnavailable(RuntimeError):
    """The optional embedding provider cannot currently serve the request."""


class OpenAICompatibleEmbeddingProvider:
    """Call a configured embeddings endpoint without coupling memory to the Agent model."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        version: str,
        dimensions: int = 1536,
        timeout_seconds: float = 6.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._model = model
        self._version = version
        self._dimensions = dimensions
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    def embed(self, text: str) -> EmbeddingResult:
        value = text.strip()[:2000]
        if not self._api_key or not value:
            raise EmbeddingUnavailable("embedding provider is not configured")
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self._model, "input": value, "dimensions": self._dimensions},
                )
            response.raise_for_status()
            vector = self._parse(response.json())
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailable("embedding provider request failed") from exc
        if len(vector) != self._dimensions:
            raise EmbeddingUnavailable("embedding provider returned an unexpected dimension")
        return EmbeddingResult(tuple(vector), self._model, self._version)

    @staticmethod
    def _parse(payload: dict[str, Any]) -> list[float]:
        raw = payload["data"][0]["embedding"]
        if not isinstance(raw, list) or any(isinstance(item, bool) for item in raw):
            raise ValueError("invalid embedding payload")
        return [float(item) for item in raw]
