"""Stable contracts shared by model-gateway implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ModelGatewayError(RuntimeError):
    """A controlled provider or model-output failure eligible for safe fallback."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        category: str = "provider_failure",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.category = category


@dataclass(frozen=True, slots=True)
class ModelAnalysis:
    intent: str
    confidence: float
    slots: dict[str, Any] = field(default_factory=dict)
    provider: str = "unknown"
    degraded: bool = False


@runtime_checkable
class ModelGateway(Protocol):
    def ready(self) -> bool:
        """Whether this gateway can classify requests (including a fallback path)."""
        ...

    def analyze(self, text: str) -> ModelAnalysis:
        """Return one structured intent-and-slots result."""
        ...

    def analyze_with_context(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
    ) -> ModelAnalysis: ...

    def classify_intent(self, text: str) -> tuple[str, float]: ...

    def extract_slots(self, text: str, intent: str) -> dict[str, Any]: ...

    def draft_announcement(
        self, *, topic: str, audience: Any, requirements: str
    ) -> dict[str, str]: ...

    def revise_announcement(
        self, *, draft: dict[str, str], audience: Any, instruction: str
    ) -> dict[str, str]: ...


class UnavailableModelGateway:
    """Explicitly unavailable gateway used to verify total-outage behavior."""

    def ready(self) -> bool:
        return False

    def analyze(self, text: str) -> ModelAnalysis:
        raise ModelGatewayError("Model gateway is unavailable")

    def analyze_with_context(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
    ) -> ModelAnalysis:
        del history, trusted_context
        return self.analyze(text)

    def classify_intent(self, text: str) -> tuple[str, float]:
        raise ModelGatewayError("Model gateway is unavailable")

    def extract_slots(self, text: str, intent: str) -> dict[str, Any]:
        raise ModelGatewayError("Model gateway is unavailable")
