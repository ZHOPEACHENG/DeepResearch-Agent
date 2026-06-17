"""
User ORM model for PostgreSQL.

Maps to the `users` table. Handles user identity, authentication state,
and account security tracking.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    institution: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    token_version: Mapped[int] = mapped_column(
        Integer, default=0,
        comment="Incremented on refresh/logout to invalidate all previously issued tokens",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tasks = relationship("ResearchTask", back_populates="user", lazy="dynamic")
    documents = relationship("Document", back_populates="user", lazy="dynamic")
    conversations = relationship("Conversation", back_populates="user", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username})>"
