"""
Shared/common Pydantic schemas for the API.

Includes generic response wrappers, error formats, and pagination helpers.
"""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error response body (RFC 7807 Problem Details)."""
    detail: str
    status_code: int
    type: str = "about:blank"
    title: str | None = None
    instance: str | None = None


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: list
    total: int
    page: int = 1
    page_size: int = 20


class MessageResponse(BaseModel):
    """Simple message response for status updates."""
    message: str
    success: bool = True


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    postgresql: bool
    mongodb: bool
    elasticsearch: bool
