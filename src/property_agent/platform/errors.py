from typing import Any

from property_agent.platform.domain.exceptions import PlatformError


class BusinessError(PlatformError):
    """Stable application error contract shared by all business modules."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.details = details
        super().__init__(code=code, message=message, status_code=status_code)
