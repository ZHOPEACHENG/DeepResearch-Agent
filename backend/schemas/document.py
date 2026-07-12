"""
Pydantic schemas for Document (knowledge base) API requests and responses.

Covers document upload, listing, search results, and QA.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# ── Enums ─────────────────────────────────────────────────────────────

VALID_FILE_TYPES = ("pdf", "docx", "txt", "md")
VALID_PROCESSING_STATUSES = ("pending", "processing", "completed", "failed")
MAX_FILE_SIZE_BYTES = 52_428_800  # 50 MB


# ── Document ──────────────────────────────────────────────────────────


class DocumentRead(BaseModel):
    """Document metadata returned to the client."""

    id: uuid.UUID
    user_id: uuid.UUID
    filename: str
    file_type: str
    file_size_bytes: int
    processing_status: str
    processing_error: str | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class DocumentList(BaseModel):
    """Paginated list of documents."""

    items: list[DocumentRead]
    total: int
    page: int = 1
    page_size: int = 20

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class DocumentUploadResponse(BaseModel):
    """Response after a successful document upload."""

    id: uuid.UUID
    filename: str
    file_type: str
    file_size_bytes: int
    processing_status: str
    message: str = "文档上传成功，正在处理中..."

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# ── Search ────────────────────────────────────────────────────────────


class SearchResultItem(BaseModel):
    """A single search result hit."""

    document_id: str
    filename: str
    chunk_index: int
    text: str
    page_number: int | None = None
    score: float | None = None
    highlights: list[str] = Field(default_factory=list)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class SearchResults(BaseModel):
    """Keyword search response."""

    query: str
    results: list[SearchResultItem]
    total: int
    took_ms: int | None = None  # ES query time

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# ── QA ────────────────────────────────────────────────────────────────


class AskRequest(BaseModel):
    """Natural language question for knowledge base QA."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="自然语言问题",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        alias="topK",
        description="检索的相关文档片段数量",
    )

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class AskSource(BaseModel):
    """Source citation for a QA answer."""

    document_id: str
    filename: str
    chunk_index: int
    excerpt: str  # relevant excerpt from the chunk
    page_number: int | None = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class AskResponse(BaseModel):
    """QA response with answer and source citations."""

    question: str
    answer: str
    sources: list[AskSource] = Field(default_factory=list)
    model: str = ""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# ── Validation helpers ────────────────────────────────────────────────


def validate_file_type(filename: str) -> str:
    """Extract and validate file extension.  Raises ValueError on unsupported types."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in VALID_FILE_TYPES:
        raise ValueError(
            f"不支持的文件格式: .{ext}。支持的格式: {', '.join(VALID_FILE_TYPES)}"
        )
    return ext
