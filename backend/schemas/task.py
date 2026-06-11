"""
Pydantic schemas for ResearchTask-related request/response validation.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ── Request Schemas ──────────────────────────────────────────────────

class TaskCreateRequest(BaseModel):
    """Request body for creating a new research task."""
    topic: str = Field(
        min_length=10,
        max_length=2000,
        examples=["大语言模型在医学诊断中的应用"],
    )


# ── Response Schemas ─────────────────────────────────────────────────

class TaskStatusRead(BaseModel):
    """Lightweight task status for list views."""
    id: UUID
    topic: str
    status: str
    current_phase: str | None = None
    progress_pct: int = 0
    progress_message: str | None = None
    elapsed_seconds: int = 0
    tags: list = []
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskRead(TaskStatusRead):
    """Full task detail including error info."""
    user_id: UUID
    error_message: str | None = None
    retry_count: int = 0
    config_json: dict = {}


class TaskListResponse(BaseModel):
    """Paginated task list response."""
    items: list[TaskStatusRead]
    total: int
    page: int = 1
    page_size: int = 20
