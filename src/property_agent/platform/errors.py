from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class BusinessError(Exception):
    """Stable application error contract shared by all business modules."""

    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message
