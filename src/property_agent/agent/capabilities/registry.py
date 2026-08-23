"""Deterministic registry for static Agent capability declarations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from property_agent.agent.capabilities.contracts import CapabilitySpec


class DuplicateCapabilityError(ValueError):
    pass


class UnknownCapabilityError(LookupError):
    pass


class CapabilityRegistry:
    def __init__(
        self,
        specs: Iterable[CapabilitySpec] = (),
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        for spec in specs:
            self.register(spec)
        self._aliases = dict(aliases or {})
        for alias, target in self._aliases.items():
            if alias in self._specs or target not in self._specs:
                raise DuplicateCapabilityError(f"invalid capability alias: {alias} -> {target}")

    def register(self, spec: CapabilitySpec) -> None:
        if spec.name in self._specs:
            raise DuplicateCapabilityError(f"capability already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec:
        name = self.resolve_name(name)
        try:
            return self._specs[name]
        except KeyError as exc:
            raise UnknownCapabilityError(f"unknown capability: {name}") from exc

    def resolve_name(self, name: str) -> str:
        return self._aliases.get(name, name)

    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    def inventory(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._specs[name] for name in sorted(self._specs))

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.inventory())
