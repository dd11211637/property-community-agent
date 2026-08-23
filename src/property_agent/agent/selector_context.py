"""Turn-scoped trusted facts for deterministic legacy graph selectors."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SelectorContext:
    roles: frozenset[str]


_current: contextvars.ContextVar[SelectorContext | None] = contextvars.ContextVar(
    "agent_selector_context",
    default=None,
)


def activate_selector_context(context: Any) -> None:
    """Project only server-supplied roles for the current orchestration turn."""
    _current.set(SelectorContext(frozenset(str(role) for role in getattr(context, "roles", ()))))


def current_selector_context() -> SelectorContext | None:
    return _current.get()
