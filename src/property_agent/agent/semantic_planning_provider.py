"""Provider I/O for bounded semantic planning and relevance judgments."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from property_agent.agent.model_contracts import ModelGatewayError
from property_agent.agent.telemetry_contracts import (
    model_schema_failure,
    observe_model_provider_attempt,
)


class SemanticPlanningClient:
    """Send strict JSON requests without owning planning normalization or authority."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str,
        model: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        total_timeout_seconds: float,
        transport: httpx.BaseTransport | None,
        observe: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._model = model
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._total_timeout = total_timeout_seconds
        self._transport = transport
        self._observe = observe or (lambda _event, _fields: None)

    def request_json(
        self,
        system_prompt: str,
        inputs: dict[str, Any],
        *,
        max_tokens: int,
        operation: str,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise ModelGatewayError("DeepSeek API key is not configured")
        last_error: Exception | None = None
        deadline = time.monotonic() + self._total_timeout
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return observe_model_provider_attempt(
                    self._observe,
                    operation,
                    lambda remaining=remaining: self._post(
                        system_prompt,
                        inputs,
                        max_tokens=max_tokens,
                        remaining_seconds=remaining,
                    ),
                )
            except ModelGatewayError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
                if attempt == 0:
                    self._observe("model_retry", {"provider": "DeepSeek", "operation": operation})
        raise ModelGatewayError("DeepSeek semantic planning failed after one retry") from last_error

    def _post(
        self,
        system_prompt: str,
        inputs: dict[str, Any],
        *,
        max_tokens: int,
        remaining_seconds: float,
    ) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(inputs, ensure_ascii=False, default=str)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        timeout = httpx.Timeout(
            connect=min(self._connect_timeout, remaining_seconds),
            read=min(self._read_timeout, remaining_seconds),
            write=min(self._read_timeout, remaining_seconds),
            pool=min(self._connect_timeout, remaining_seconds),
        )
        try:
            with httpx.Client(transport=self._transport, timeout=timeout) as client:
                response = client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ModelGatewayError("DeepSeek semantic-planner transport failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ModelGatewayError(f"DeepSeek retryable HTTP status {response.status_code}")
        if response.is_error:
            raise ModelGatewayError(
                f"DeepSeek semantic-planner HTTP status {response.status_code}", retryable=False
            )
        try:
            content = response.json()["choices"][0]["message"]["content"]
            value = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise model_schema_failure("DeepSeek returned invalid semantic planning JSON") from exc
        if not isinstance(value, dict):
            raise model_schema_failure("DeepSeek semantic planning output must be an object")
        return value
