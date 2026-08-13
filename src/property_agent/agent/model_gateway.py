"""Public compatibility facade for model gateway implementations.

Callers should continue importing from this module. Implementations live in focused
modules so provider I/O, deterministic parsing, and fallback policy can evolve independently.
"""

from property_agent.agent.deepseek_gateway import DeepSeekModelGateway
from property_agent.agent.deterministic_gateway import DeterministicModelGateway
from property_agent.agent.fallback_gateway import FallbackModelGateway
from property_agent.agent.model_contracts import (
    ModelAnalysis,
    ModelGateway,
    ModelGatewayError,
    UnavailableModelGateway,
)

__all__ = [
    "DeepSeekModelGateway",
    "DeterministicModelGateway",
    "FallbackModelGateway",
    "ModelAnalysis",
    "ModelGateway",
    "ModelGatewayError",
    "UnavailableModelGateway",
]
