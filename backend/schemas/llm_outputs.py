"""
Pydantic schemas for LLM structured output.

Each schema replaces a ``safe_json_loads()`` + manual field extraction +
normalization pattern currently scattered across agents.  Using
``ChatOpenAI.with_structured_output()`` with these schemas guarantees
valid JSON at the API level — no more parsing fallback code.

Usage::

    from backend.tools.llm import get_chat_model
    from backend.schemas.llm_outputs import PlanOutput

    model = get_chat_model("planner", temperature=0.3)
    structured = model.with_structured_output(PlanOutput, method="json_schema")
    output: PlanOutput = await structured.ainvoke(messages)
    # output.research_questions, output.search_keywords, etc. are typed
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── Planner ────────────────────────────────────────────────────────────


class SubQuestionOutput(BaseModel):
    """A sub-question nested under a core research question."""

    id: str = Field(default="", description="Stable short id, e.g. q1.1")
    question: str
    priority: int = 1


class ResearchQuestionOutput(BaseModel):
    """A core research question with optional sub-questions."""

    id: str = Field(default="", description="Stable short id, e.g. q1")
    question: str
    sub_questions: list[SubQuestionOutput] = Field(default_factory=list)


class SearchKeywordOutput(BaseModel):
    """A search keyword with language and priority metadata."""

    keyword: str
    language: Literal["en", "zh"] = "en"
    priority: int = 1


class PlanOutput(BaseModel):
    """Structured research plan produced by the Planner agent."""

    research_questions: list[ResearchQuestionOutput]
    search_keywords: list[SearchKeywordOutput]
    expected_sources: list[str] = Field(
        default=["web", "arxiv", "semantic_scholar"],
    )


class ClarityCheckOutput(BaseModel):
    """Result of the pre-plan clarity check."""

    is_clear: bool = True
    clarifying_question: str = ""


# ── Synthesizer ────────────────────────────────────────────────────────


class ConflictItem(BaseModel):
    """A single conflict between sources and its resolution."""

    topic: str = ""
    finding_a: str = ""
    finding_b: str = ""
    resolution: str = ""


class SynthesizerOutput(BaseModel):
    """Structured knowledge base produced by the Synthesizer agent."""

    synthesized_content: str = Field(
        description="Markdown: the final merged, conflict-resolved knowledge base",
    )
    conflicts: list[ConflictItem] = Field(default_factory=list)
    citation_map: dict[str, list[str]] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)


# ── Writer ─────────────────────────────────────────────────────────────


class SectionOutput(BaseModel):
    """A single report section with inline citation indices."""

    heading: str
    content: str = Field(description="Markdown body with inline [N] citations")
    citation_indices: list[int] = Field(default_factory=list)


class WriterOutput(BaseModel):
    """Structured research report produced by the Writer agent."""

    title: str = "研究报告"
    abstract: str = ""
    background: str = ""
    sections: list[SectionOutput] = Field(default_factory=list)
    gap_notes: str = ""


# ── Modification Classification ────────────────────────────────────────


class ModificationClassifyOutput(BaseModel):
    """Result of the B-plan modify router classification."""

    mode: Literal["augment", "revise"]


# ── Analyzer ──────────────────────────────────────────────────────────


class KnowledgeGapItem(BaseModel):
    """A single knowledge gap identified by the Analyzer."""

    related_question_id: str = ""
    description: str
    severity: Literal["critical", "moderate", "minor"] = "moderate"
    suggested_query: str = ""


class AnalyzerOutput(BaseModel):
    """Structured knowledge integration output from the Analyzer agent.

    Replaces the former ``AnalysisComplete`` tool call.  Used with
    ``model.with_structured_output(AnalyzerOutput)``.
    """

    summary_content: str = Field(
        description="Markdown 格式的完整知识整合摘要",
    )
    citation_map: dict[str, list[str]] = Field(
        description='知识块 ID 到检索结果 ID 列表的映射，如 {"chunk_1": ["id_a", "id_b"]}',
    )
    knowledge_gaps: list[KnowledgeGapItem] = Field(default_factory=list)
