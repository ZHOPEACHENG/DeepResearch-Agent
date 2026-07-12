"""
Conversation and Message ORM models for PostgreSQL.

Conversation: Top-level chat session container.
Message: Individual interaction within a conversation.
ResearchTask is linked via message_id FK (hidden from user-facing UI).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, default="New Conversation",
    )
    model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="gpt-4o",
    )
    context_window_tokens: Mapped[int] = mapped_column(
        Integer, default=0,
    )
    tags: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    # Relationships
    user = relationship("User", back_populates="conversations")
    messages = relationship(
        "Message", back_populates="conversation",
        order_by="Message.created_at", lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, title={self.title[:30]}...)>"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
    )
    message_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="text",
    )
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict,
    )
    token_count: Mapped[int] = mapped_column(
        Integer, default=0,
    )
    model: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    parent_message = relationship(
        "Message", remote_side="Message.id", backref="replies",
    )

    # Valid message types
    VALID_MESSAGE_TYPES = (
        "text", "plan_card", "retrieval_card", "report_card",
        "citation", "error", "gap_question",
    )

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, role={self.role}, type={self.message_type})>"
