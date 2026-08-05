from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class Envelope(BaseModel):
    success: bool
    data: Any = None
    error: ErrorBody | None = None
    request_id: str
