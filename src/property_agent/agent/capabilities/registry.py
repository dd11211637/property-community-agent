"""Deterministic registry for static Agent capability declarations."""

from __future__ import annotations

from collections.abc import Iterable

from property_agent.agent.capabilities.contracts import CapabilitySpec


class DuplicateCapabilityError(ValueError):
    pass


class UnknownCapabilityError(LookupError):
    pass


class CapabilityRegistry:
    def __init__(self, specs: Iterable[CapabilitySpec] = ()) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: CapabilitySpec) -> None:
        if spec.name in self._specs:
            raise DuplicateCapabilityError(f"capability already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise UnknownCapabilityError(f"unknown capability: {name}") from exc

    def inventory(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._specs[name] for name in sorted(self._specs))

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.inventory())
