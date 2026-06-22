"""
Pydantic schemas for Conversation, Message, and SSE events.

Conversation: Top-level chat session container.
Message: Individual interaction with polymorphic message types.
SSE: Streaming events emitted during chat/research execution.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# ── Conversation ────────────────────────────────────────────────────────

class ConversationCreate(BaseModel):
    """Request body for creating a new conversation."""
    title: str | None = Field(None, max_length=200)
    model: str | None = None


class ConversationUpdate(BaseModel):
    """Request body for updating a conversation."""
    title: str = Field(min_length=1, max_length=200)


class ConversationRead(BaseModel):
    """Conversation summary for sidebar list."""
    id: UUID
    title: str
    model: str
    message_count: int = 0
    last_message_preview: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class ConversationListResponse(BaseModel):
    """Paginated conversation list."""
    items: list[ConversationRead]
    total: int
    page: int = 1
    page_size: int = 20

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# ── Message ─────────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    """Request body for sending a message (triggers SSE streaming)."""
    content: str = Field(min_length=1, max_length=10000)
    parent_message_id: UUID | None = None
    model: str | None = None
    mode: Literal["chat", "research"] = "chat"


class PlanActionRequest(BaseModel):
    """Request body for acting on a research plan."""
    action: Literal["accept", "modify", "reject"]
    modifications: str | None = Field(None, description="Required if action=modify")


class MessageRead(BaseModel):
    """Message as returned to the client."""
    id: UUID
    conversation_id: UUID
    role: str  # user / assistant / system / tool
    content: str
    message_type: str = "text"
    parent_message_id: UUID | None = None
    extra: dict = Field(default_factory=dict, serialization_alias="metadata")
    token_count: int = 0
    model: str | None = None
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class MessageListResponse(BaseModel):
    """Cursor-based paginated message list."""
    items: list[MessageRead]
    has_more: bool = False

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# ── SSE Events ──────────────────────────────────────────────────────────

class SSEEvent(BaseModel):
    """A single SSE event emitted during chat/research streaming."""
    event: str = Field(
        description="SSE event type (message_created, chat_chunk, "
                    "plan_generated, retrieval_started, retrieval_progress, "
                    "retrieval_complete, analysis_complete, gap_question, "
                    "report_chunk, report_complete, error, done)",
    )
    data: dict = Field(default_factory=dict)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
