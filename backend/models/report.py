"""
ResearchReport and Citation ORM models for PostgreSQL.

ResearchReport: Final research output linked to a task.
Citation: Individual source references within a report.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class ResearchReport(Base):
    __tablename__ = "research_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_tasks.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    sections_json: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    citations_json: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    gap_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    export_format_log: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    task = relationship("ResearchTask", back_populates="report")
    citations = relationship("Citation", back_populates="report", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<ResearchReport(id={self.id}, task_id={self.task_id})>"


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    index_number: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_result_id: Mapped[str] = mapped_column(
        String(24), nullable=False
    )
    context_in_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    report = relationship("ResearchReport", back_populates="citations")

    def __repr__(self) -> str:
        return f"<Citation(id={self.id}, report_id={self.report_id}, index={self.index_number})>"
