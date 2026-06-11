"""
Research workflow state schema and phase-specific data models.

Defines the TypedDict for the LangGraph research workflow state,
plus Pydantic schemas for each stage's input/output data.
"""

from datetime import datetime
from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel, Field


# ── Research Workflow State (LangGraph TypedDict) ────────────────────

class ResearchState(TypedDict, total=False):
    """
    Shared state passed between LangGraph nodes during research execution.

    Fields are optional (total=False) because each stage adds its outputs.
    """
    task_id: str
    user_id: str
    topic: str

    # Phase outputs
    research_plan: dict | None
    retrieval_results: list[dict] | None
    round_number: int
    knowledge_summary: dict | None
    knowledge_gaps: list[dict] | None
    synthesized_knowledge: dict | None
    final_report: dict | None

    # Control
    error: str | None
    current_phase: str


# ── Phase-Specific Schemas ───────────────────────────────────────────

class ResearchQuestion(BaseModel):
    """A single research question in the hierarchical tree."""
    id: str
    text: str
    priority: int = 1
    sub_questions: list["ResearchQuestion"] = []


class ResearchPlanSchema(BaseModel):
    """Output of the Planner agent."""
    task_id: UUID
    topic: str
    questions: list[ResearchQuestion] = []
    search_keywords: list[str] = []
    priority_order: list[str] = []
    generated_at: datetime | None = None


class RetrievalResultSchema(BaseModel):
    """A single search result from any source."""
    result_id: UUID
    task_id: UUID
    round_number: int
    title: str
    abstract: str = ""
    excerpt: str = ""
    source_url: str = ""
    doi: str | None = None
    source_type: str = ""
    authors: list[str] = []
    publication_date: str | None = None
    credibility: str = "unknown"
    snapshot_text: str = ""
    retrieved_at: datetime | None = None


class KnowledgeSummarySchema(BaseModel):
    """Output of the Analyzer agent — integrated knowledge."""
    task_id: UUID
    phase: str
    summary_content: str
    citation_map: dict[str, str] = {}  # knowledge_chunk_id → retrieval_result_id
    generated_at: datetime | None = None


class KnowledgeGapSchema(BaseModel):
    """A single knowledge gap identified by the Analyzer."""
    task_id: UUID
    gap_id: UUID
    description: str
    related_question_id: str = ""
    triggered_retrieval: bool = False
    retrieval_status: str = "pending"
    created_at: datetime | None = None


class StageOutputs(BaseModel):
    """Aggregated outputs for a research task (all stages)."""
    task_id: UUID
    plan: ResearchPlanSchema | None = None
    retrieval_rounds: list[list[RetrievalResultSchema]] = []
    knowledge_summaries: list[KnowledgeSummarySchema] = []
    knowledge_gaps: list[KnowledgeGapSchema] = []
