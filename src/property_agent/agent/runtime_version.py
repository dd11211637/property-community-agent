"""Canonical runtime identity for the V2-only Agent service."""

from enum import StrEnum


class AgentRuntimeVersion(StrEnum):
    """The only runtime accepted by production and persisted conversations."""

    V2 = "v2"

    @classmethod
    def from_str(cls, value: str | None) -> "AgentRuntimeVersion":
        normalized = cls.V2.value if value is None else str(value).strip().lower()
        if normalized != cls.V2.value:
            raise ValueError(f"retired agent runtime version: {value!r}")
        return cls.V2
