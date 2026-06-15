"""
Shared/common Pydantic schemas for the API.

Includes generic response wrappers, error formats, and pagination helpers.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ErrorResponse(BaseModel):
    """Standard error response body (RFC 7807 Problem Details)."""
    detail: str
    status_code: int
    type: str = "about:blank"
    title: str | None = None
    instance: str | None = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: list
    total: int
    page: int = 1
    page_size: int = 20

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class MessageResponse(BaseModel):
    """Simple message response for status updates."""
    message: str
    success: bool = True

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    postgresql: bool
    mongodb: bool
    elasticsearch: bool

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
